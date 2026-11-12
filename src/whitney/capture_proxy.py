"""The logging proxy a local run with capture on sends its model calls through (Phase 5 Task 31).

It listens on 127.0.0.1, on a port the system picks, and forwards every
request to the one upstream it was started with, never to a host a request
names. Each request's body goes to a capture.Capture as it passes; its
headers go on to the upstream as they came and are never handed to the
capture or kept once forwarded, so the harness's own key, sent in a header,
is passed on and not recorded. The proxy adds no credential of its own: a
caller that sends none, such as the agent's shell, gets the upstream's
refusal. Answers are passed back as they arrive, so a streamed (SSE) answer
reaches the harness event by event, and no answer is recorded.

The upstreams are the providers' public API addresses: the harness is given
the proxy's origin alone, so each path it sends is the one it would send
without a proxy, after the upstream's own path (docs/results/
2026-11-12-capture-followups.md, decision 6). The proxy connects to the
upstream directly, not through HTTPS_PROXY.
"""

import contextlib
import http.client
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from whitney.capture import Capture

LOOPBACK = "127.0.0.1"
# Where each provider's calls go, by the provider part of a Kilo model, and Claude Code's.
UPSTREAMS = {
    "kilo": "https://api.kilo.ai",  # the Kilo Gateway: Kilo sends /api/openrouter/... to it (decision 6)
    "anthropic": "https://api.anthropic.com/v1",  # Kilo's anthropic provider sends /messages after its /v1 base URL
    "openai": "https://api.openai.com/v1",
}
CLAUDE_CODE_UPSTREAM = "https://api.anthropic.com"  # Claude Code sends /v1/messages after ANTHROPIC_BASE_URL
UPSTREAM_TIMEOUT = 600  # seconds a single read from the upstream may take; a streamed answer can pause while the model thinks
CHUNK = 65536
POLL_INTERVAL = 0.05  # seconds; how long the end of a run waits for the proxy to notice it should stop (the default is 0.5)
# Hop-by-hop headers (RFC 9110, 7.6.1) belong to one connection, so neither direction passes them on.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade",
})  # fmt: skip
# A request's Host and length are set again for the upstream; Expect was answered here already.
NOT_FORWARDED = HOP_BY_HOP | {"host", "content-length", "expect"}


class _Handler(BaseHTTPRequestHandler):
    """Forwards one connection's requests to `upstream` and hands each body to `capture`."""

    protocol_version = "HTTP/1.1"  # keep-alive and chunked answers, as the harness's own client expects
    upstream: urllib.parse.SplitResult
    capture: Capture

    def log_message(self, format, *args) -> None:
        """Write nothing: the default writes each request line to stderr, which a run keeps in its log."""

    def _forward(self) -> None:
        if not self.path.startswith("/"):  # an absolute URL or an authority names its own destination
            self.send_error(400, "only a path is forwarded")
            return
        body = self._body()
        self.capture.add(body, self.command, self.path)
        connection_class = http.client.HTTPSConnection if self.upstream.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.upstream.netloc, timeout=UPSTREAM_TIMEOUT)
        try:
            connection.putrequest(self.command, self.upstream.path.rstrip("/") + self.path, skip_accept_encoding=True)
            for name, value in self.headers.items():
                if name.lower() not in NOT_FORWARDED:
                    connection.putheader(name, value)
            if "content-length" in self.headers or "transfer-encoding" in self.headers:
                connection.putheader("Content-Length", str(len(body)))
            connection.endheaders(body)
            answer = connection.getresponse()
        except OSError as error:
            connection.close()
            self.send_error(502, f"Whitney's capture proxy couldn't reach {self.upstream.netloc}: {type(error).__name__}")
            return
        try:
            self._relay(answer)
        finally:
            connection.close()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

    def _body(self) -> bytes:
        """The request's body, read whole: by its length, or decoded from chunks."""
        if "chunked" in self.headers.get("transfer-encoding", "").lower():
            parts = []
            while size := int(self.rfile.readline().split(b";")[0], 16):
                parts.append(self.rfile.read(size))
                self.rfile.readline()  # the line end after each chunk
            while self.rfile.readline().strip():  # any trailer lines, up to the blank line that ends them
                pass
            return b"".join(parts)
        return self.rfile.read(int(self.headers.get("content-length") or 0))

    def _relay(self, answer: http.client.HTTPResponse) -> None:
        """Pass `answer` back as it arrives: its status and headers, then its body, piece by piece."""
        self.send_response_only(answer.status, answer.reason)
        has_body = self.command != "HEAD" and answer.status not in (204, 304)
        chunked = has_body and answer.getheader("content-length") is None
        for name, value in answer.getheaders():
            if name.lower() not in HOP_BY_HOP:
                self.send_header(name, value)
        if chunked:  # http.client has taken the upstream's chunks apart, so they are made again here
            self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while has_body and (piece := answer.read1(CHUNK)):
            self.wfile.write(b"%X\r\n%s\r\n" % (len(piece), piece) if chunked else piece)
            self.wfile.flush()
        if chunked:
            self.wfile.write(b"0\r\n\r\n")


class _Server(ThreadingHTTPServer):
    daemon_threads = True  # a connection the harness left open doesn't keep the run waiting when it ends


@contextlib.contextmanager
def logging_proxy(upstream: str, capture: Capture) -> Iterator[str]:
    """A logging proxy to `upstream` for the length of the block; yields its origin, http://127.0.0.1:<port>.

    Args:
        upstream: the one address requests are forwarded to, with any path
            to put before each request's own, such as https://api.anthropic.com/v1.
        capture: given each request's method, path and body.

    Side effects: serves on a thread of its own, and stops serving and frees
    the port when the block ends, however it ends.
    """
    handler = type("Handler", (_Handler,), {"upstream": urllib.parse.urlsplit(upstream), "capture": capture})
    server = _Server((LOOPBACK, 0), handler)
    thread = threading.Thread(target=server.serve_forever, args=(POLL_INTERVAL,), name="whitney-capture-proxy", daemon=True)
    thread.start()
    try:
        yield f"http://{LOOPBACK}:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
