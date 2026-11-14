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
import re
import sys
import tempfile
import threading
import urllib.parse
from collections.abc import Callable, Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from whitney.capture import VIA_PROXY, Capture, Watch

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
MAX_LINE = 1024  # bytes in a chunk-size or trailer line; a real one is a few
SPOOLED_IN_MEMORY = 1024 * 1024  # bytes of a request body held in memory before the rest goes to a temporary file
# The largest body forwarded: past it the proxy answers 413 without reading it. Anthropic's Messages API takes
# requests of up to 32 MB, so no model call a harness sends comes near it.
MAX_FORWARDED_BODY = 256 * 1024 * 1024
POLL_INTERVAL = 0.05  # seconds; how long the end of a run waits for the proxy to notice it should stop (the default is 0.5)
# Hop-by-hop headers (RFC 9110, 7.6.1) belong to one connection, so neither direction passes them on.
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade",
})  # fmt: skip
# A header the upstream folded over several lines (obs-fold): a proxy replaces each fold with one space before
# passing it on (RFC 9112, 5.2). http.client keeps a fold in the value, after a CRLF, a bare LF or a bare CR,
# and send_header writes it as it is, so a client could otherwise read the continuation line as a header of
# its own. Any other line break ends the header in http.client, and it keeps every CR and LF out of names.
OBS_FOLD = re.compile(r"[\r\n]+[ \t]*")
# A request's Host and length are set again for the upstream; Expect was answered here already.
NOT_FORWARDED = HOP_BY_HOP | {"host", "content-length", "expect"}
# Header names and values the proxy reads or writes. HTTP compares names, and these values, ignoring case.
CONTENT_LENGTH = "Content-Length"
TRANSFER_ENCODING = "Transfer-Encoding"
CONTENT_ENCODING = "Content-Encoding"
CHUNKED = "chunked"
CONNECTION = "Connection"
CLOSE = "close"


class _Refused(Exception):
    """A request the proxy answers itself, with `status`, without forwarding it."""

    def __init__(self, status: int, reason: str) -> None:
        self.status, self.reason = status, reason


class _Body:
    """A request's body as it is read: spooled whole for the upstream, its first `kept` bytes held for the capture.

    The spool keeps SPOOLED_IN_MEMORY bytes in memory and the rest on disk,
    so a large body costs the proxy at most the capture's cap and a chunk
    in memory, and is still forwarded whole, byte for byte. Its size is
    checked against MAX_FORWARDED_BODY before it is read (_Handler._body).
    """

    def __init__(self, kept: int) -> None:
        self.spool = tempfile.SpooledTemporaryFile(max_size=SPOOLED_IN_MEMORY)
        self.kept = bytearray()
        self.size = 0
        self._limit = kept

    def write(self, piece: bytes) -> None:
        self.size += len(piece)
        self.spool.write(piece)
        self.kept += piece[: max(0, self._limit - len(self.kept))]

    def pieces(self) -> Iterator[bytes]:
        """The whole body again, from the start, CHUNK bytes at a time."""
        self.spool.seek(0)
        while piece := self.spool.read(CHUNK):
            yield piece


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
        try:
            body = self._body()
        except _Refused as refused:  # send_error closes the connection, since the rest of the body is unread
            self.send_error(refused.status, refused.reason)
            return
        with body.spool:
            self.capture.add(bytes(body.kept), self.command, self.path, size=body.size, encoding=self.headers.get(CONTENT_ENCODING))
            self._send_upstream(body)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _forward

    def _send_upstream(self, body: _Body) -> None:
        """Send the request to the upstream with `body`, and relay its answer."""
        connection_class = http.client.HTTPSConnection if self.upstream.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(self.upstream.netloc, timeout=UPSTREAM_TIMEOUT)
        try:
            connection.putrequest(self.command, self.upstream.path.rstrip("/") + self.path, skip_accept_encoding=True)
            for name, value in self.headers.items():
                if name.lower() not in NOT_FORWARDED:
                    connection.putheader(name, value)
            if CONTENT_LENGTH in self.headers or TRANSFER_ENCODING in self.headers:
                connection.putheader(CONTENT_LENGTH, str(body.size))
            connection.endheaders()
            for piece in body.pieces():
                connection.send(piece)
            answer = connection.getresponse()
        except OSError as error:
            connection.close()
            self.send_error(502, f"Whitney's capture proxy couldn't reach {self.upstream.netloc}: {type(error).__name__}")
            return
        try:
            self._relay(answer)
        finally:
            connection.close()

    def _body(self) -> _Body:
        """The request's body, read whole: by its one Content-Length, or decoded from chunks.

        Raises:
            _Refused: 400 for framing that is malformed or cut short (a length
                that isn't one whole number, lengths that differ, a transfer
                coding other than chunked alone (empty list elements are
                ignored, as RFC 9110 5.6.1 says), a chunk size that isn't
                hexadecimal or a line over MAX_LINE); 413 for a body over
                MAX_FORWARDED_BODY. Nothing negative or unbounded is read.
        """
        body = _Body(self.capture.kept_bytes)
        values = self.headers.get_all(TRANSFER_ENCODING) or []
        codings = [coding.strip().lower() for value in values for coding in value.split(",") if coding.strip()]
        if values:
            if codings != [CHUNKED]:
                raise _Refused(400, "only the chunked transfer coding is read")
            self._read_chunks(body)
            return body
        lengths = {value.strip() for value in self.headers.get_all(CONTENT_LENGTH) or []}
        if not lengths:
            return body
        length = lengths.pop()
        if lengths or not re.fullmatch(r"[0-9]+", length):  # ASCII digits only: str.isdigit takes "²", which int refuses
            raise _Refused(400, "Content-Length must be one whole number")
        size = int(length)
        if size > MAX_FORWARDED_BODY:
            raise _Refused(413, f"a body over {MAX_FORWARDED_BODY} bytes isn't forwarded")
        self._read_exactly(size, body)
        return body

    def _read_chunks(self, body: _Body) -> None:
        """Read a chunked body into `body`, then any trailer lines up to the blank line that ends them."""
        while True:
            size_text = self._line().split(b";")[0].strip()
            if not re.fullmatch(rb"[0-9A-Fa-f]{1,16}", size_text):
                raise _Refused(400, "a chunk size must be hexadecimal")
            size = int(size_text, 16)
            if size == 0:
                break
            if body.size + size > MAX_FORWARDED_BODY:
                raise _Refused(413, f"a body over {MAX_FORWARDED_BODY} bytes isn't forwarded")
            self._read_exactly(size, body)
            if self._line().strip():
                raise _Refused(400, "a chunk must end with a line end")
        while self._line().strip():
            pass

    def _line(self) -> bytes:
        """One line of the request, at most MAX_LINE bytes."""
        line = self.rfile.readline(MAX_LINE + 1)
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            raise _Refused(400, "a framing line was too long or cut short")
        return line

    def _read_exactly(self, size: int, body: _Body) -> None:
        """Read `size` bytes of body into `body`, CHUNK bytes at a time."""
        while size:
            piece = self.rfile.read(min(size, CHUNK))
            if not piece:
                raise _Refused(400, "the body ended before its length")
            body.write(piece)
            size -= len(piece)

    def _relay(self, answer: http.client.HTTPResponse) -> None:
        """Pass `answer` back as it arrives: its status and headers, then its body, piece by piece.

        An answer with no length is chunked again for an HTTP/1.1 client;
        an HTTP/1.0 client, which can't read chunks, gets the body as it
        comes, ended by closing the connection.
        """
        self.send_response_only(answer.status, answer.reason)
        has_body = self.command != "HEAD" and answer.status not in (204, 304)
        unsized = has_body and answer.getheader(CONTENT_LENGTH) is None
        chunked = unsized and self.request_version != "HTTP/1.0"
        for name, value in answer.getheaders():
            if name.lower() not in HOP_BY_HOP:
                self.send_header(name, OBS_FOLD.sub(" ", value))
        if chunked:  # http.client has taken the upstream's chunks apart, so they are made again here
            self.send_header(TRANSFER_ENCODING, CHUNKED)
        elif unsized:
            self.send_header(CONNECTION, CLOSE)
            self.close_connection = True
        self.end_headers()
        while has_body and (piece := answer.read1(CHUNK)):
            self.wfile.write(b"%X\r\n%s\r\n" % (len(piece), piece) if chunked else piece)
            self.wfile.flush()
        if chunked:
            self.wfile.write(b"0\r\n\r\n")


class _Server(ThreadingHTTPServer):
    daemon_threads = True  # a connection the harness left open doesn't keep the run waiting when it ends

    def handle_error(self, *request_and_address) -> None:
        """Say nothing of a client that went before its answer was written; report anything else as the server does."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(*request_and_address)


def kilo_upstream(model: str, global_provider: Mapping[str, dict]) -> str:
    """Where a captured local Kilo run's calls for `model` go: its provider's base URL in the global config, else UPSTREAMS'.

    A base URL the user's own config sets for the provider is kept as the
    upstream, so capture doesn't change where the run's calls go.

    Raises:
        ValueError: neither has one for the provider.
    """
    provider = model.partition("/")[0]
    configured = ((global_provider.get(provider) or {}).get("options") or {}).get("baseURL")
    if configured:
        return configured
    if provider not in UPSTREAMS:
        raise ValueError(
            f"--capture-prompt on the kilo target knows the API address of {', '.join(f'{name}/' for name in UPSTREAMS)} "
            f"models only, or of a provider whose baseURL your global Kilo config sets; got {model!r}"
        )
    return UPSTREAMS[provider]


@contextlib.contextmanager
def proxied(watch: Watch | None, upstream: str, redact: Callable[[str], str]) -> Iterator[tuple[str | None, Capture | None]]:
    """A logging proxy to `upstream` if `watch` has capture on, else nothing: yields (its origin, its Capture), or (None, None).

    Args:
        redact: as Capture's, so the redacted texts in `watch` are found.
    """
    if watch is None or not watch.capture:
        yield None, None
        return
    capture = Capture(VIA_PROXY, watch.texts, redact, watch.model)
    with logging_proxy(upstream, capture) as origin:
        yield origin, capture


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
