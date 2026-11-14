"""The logging proxy a captured local run sends its model calls through (Phase 5 Task 31).

A fake upstream on the loopback address stands in for the provider's API, so
these tests see exactly what the proxy forwards and what it gives back. The
real upstreams are fixed in capture_proxy.UPSTREAMS; the proxy forwards to
whatever single upstream it was started with and to nothing else.
"""

import http.client
import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from whitney.capture import VIA_PROXY, Capture
from whitney.capture_proxy import logging_proxy

KEY = "sk-ant-fake-key-for-the-proxy-tests-0123"
TIMEOUT = 10  # seconds: every socket here is local, so a wait this long means a hang


class Upstream:
    """A fake provider API: records each request and answers as `reply` says."""

    def __init__(self) -> None:
        self.seen: list[dict] = []
        self.reply = {"status": 200, "body": b'{"ok": true}', "headers": {"content-type": "application/json"}}
        self.release = threading.Event()  # a streamed reply waits for it before its last event
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # the default writes every request to stderr
                pass

            def _answer(self):
                length = int(self.headers.get("content-length") or 0)
                outer.seen.append({
                    "method": self.command, "path": self.path, "headers": list(self.headers.items()),
                    "body": self.rfile.read(length),
                })
                if outer.reply.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("transfer-encoding", "chunked")
                    self.end_headers()
                    self._chunk(b"event: first\ndata: 1\n\n")
                    outer.release.wait(TIMEOUT)
                    self._chunk(b"event: last\ndata: 2\n\n")
                    self.wfile.write(b"0\r\n\r\n")
                    return
                self.send_response(outer.reply["status"])
                for name, value in outer.reply["headers"].items():
                    self.send_header(name, value)
                self.send_header("content-length", str(len(outer.reply["body"])))
                self.end_headers()
                self.wfile.write(outer.reply["body"])

            def _chunk(self, data: bytes):
                self.wfile.write(f"{len(data):X}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()

            do_GET = do_POST = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def headers(self, index: int = 0) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for name, value in self.seen[index]["headers"]:
            found.setdefault(name.lower(), []).append(value)
        return found


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.release.set()
    server.server.shutdown()
    server.server.server_close()


def client(origin: str) -> http.client.HTTPConnection:
    address = urllib.parse.urlsplit(origin)
    return http.client.HTTPConnection(address.hostname, address.port, timeout=TIMEOUT)


def raw_request(origin: str, data: bytes) -> bytes:
    """Send `data` as it is to the proxy and return everything it answers before closing."""
    address = urllib.parse.urlsplit(origin)
    with socket.create_connection((address.hostname, address.port), timeout=TIMEOUT) as connection:
        connection.sendall(data)
        answer = b""
        while chunk := connection.recv(65536):
            answer += chunk
        return answer


def test_the_proxy_listens_on_the_loopback_address_only_on_a_port_the_system_chose(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as first, logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as second:
        addresses = [urllib.parse.urlsplit(origin) for origin in (first, second)]

    assert [address.hostname for address in addresses] == ["127.0.0.1", "127.0.0.1"]
    assert addresses[0].port != addresses[1].port and all(address.port for address in addresses)
    assert [address.path for address in addresses] == ["", ""]  # the origin alone, which the harness is given


def test_a_request_reaches_the_upstream_with_its_path_body_and_headers_and_its_answer_comes_back(upstream):
    upstream.reply = {"status": 201, "body": b'{"id": "msg_1"}', "headers": {"content-type": "application/json", "x-request-id": "r1"}}
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()

    with logging_proxy(upstream.url + "/v1", Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        connection.request("POST", "/messages?beta=true", body=body, headers={"x-api-key": KEY, "anthropic-beta": "a", "content-type": "application/json"})
        answer = connection.getresponse()
        got = (answer.status, answer.getheader("x-request-id"), answer.read())

    assert got == (201, "r1", b'{"id": "msg_1"}')
    assert upstream.seen[0]["method"] == "POST"
    assert upstream.seen[0]["path"] == "/v1/messages?beta=true"  # the upstream's own path, then the request's
    assert upstream.seen[0]["body"] == body
    assert upstream.headers()["x-api-key"] == [KEY]  # the harness's own key, passed on as it came
    assert upstream.headers()["anthropic-beta"] == ["a"]


def test_a_header_sent_twice_is_passed_on_twice(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        connection.putrequest("GET", "/models", skip_accept_encoding=True)
        connection.putheader("anthropic-beta", "one")
        connection.putheader("anthropic-beta", "two")
        connection.endheaders()
        connection.getresponse().read()

    assert upstream.headers()["anthropic-beta"] == ["one", "two"]
    assert "content-length" not in upstream.headers()  # a GET without a body is passed on without one


def test_the_host_the_request_names_is_never_where_it_goes(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        connection.putrequest("GET", "/models", skip_host=True)
        connection.putheader("Host", "evil.invalid")
        connection.endheaders()
        connection.getresponse().read()

    assert upstream.headers()["host"] == [urllib.parse.urlsplit(upstream.url).netloc]


@pytest.mark.parametrize("target", [b"http://evil.invalid/steal", b"evil.invalid:443", b"*"])
def test_a_request_that_names_its_own_destination_is_refused_and_not_forwarded(upstream, target):
    capture = Capture(VIA_PROXY, {})
    with logging_proxy(upstream.url, capture) as origin:
        answer = raw_request(origin, b"GET " + target + b" HTTP/1.1\r\nHost: evil.invalid\r\nConnection: close\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 400")
    assert upstream.seen == []
    assert capture.record()["requests"] == []


def test_a_connect_request_is_not_served(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = raw_request(origin, b"CONNECT evil.invalid:443 HTTP/1.1\r\nHost: evil.invalid:443\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 501")
    assert upstream.seen == []


def test_the_proxy_adds_no_credential_of_its_own(upstream):
    """The agent's shell can reach the proxy; a call through it carries only what the caller sent, so it gains no authority."""
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages", body=b'{"messages": []}')
        connection.getresponse().read()

    assert not {"x-api-key", "authorization", "cookie"} & set(upstream.headers())


def test_header_values_never_reach_the_capture(upstream):
    capture = Capture(VIA_PROXY, {})
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()

    with logging_proxy(upstream.url, capture) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages", body=body, headers={"x-api-key": KEY, "authorization": f"Bearer {KEY}", "cookie": KEY})
        connection.getresponse().read()

    assert KEY not in json.dumps(capture.record())
    assert capture.record()["requests"] == [{"method": "POST", "path": "/v1/messages", "bytes": len(body), "model_request": True}]


def test_a_streamed_answer_reaches_the_harness_before_the_upstream_has_finished_it(upstream):
    upstream.reply = {"stream": True}

    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages", body=b'{"messages": [], "stream": true}')
        answer = connection.getresponse()
        first = answer.read1(65536)  # returns what has arrived; the upstream is still holding its last event
        upstream.release.set()
        rest = answer.read()

    assert first == b"event: first\ndata: 1\n\n"
    assert rest == b"event: last\ndata: 2\n\n"
    assert answer.getheader("content-type") == "text/event-stream"


def test_an_answer_the_upstream_refuses_is_passed_on_as_it_came(upstream):
    upstream.reply = {"status": 401, "body": b'{"error": "invalid x-api-key"}', "headers": {"content-type": "application/json"}}

    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages", body=b"{}")
        answer = connection.getresponse()
        got = (answer.status, answer.read())

    assert got == (401, b'{"error": "invalid x-api-key"}')


def test_a_body_sent_in_chunks_is_passed_on_whole(upstream):
    capture = Capture(VIA_PROXY, {})
    with logging_proxy(upstream.url, capture) as origin:
        answer = raw_request(origin, (
            b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
            b"5;note=1\r\n{\"mes\r\n9\r\nsages\": [\r\n2\r\n]}\r\n0\r\nTrailer: x\r\n\r\n"
        ))

    assert answer.startswith(b"HTTP/1.1 200")
    assert upstream.seen[0]["body"] == b'{"messages": []}'
    assert "transfer-encoding" not in upstream.headers()
    assert capture.record()["requests"][0]["model_request"] is True


def test_several_requests_on_one_connection_are_each_forwarded(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        connection = client(origin)
        for path in ("/a", "/b"):
            connection.request("GET", path)
            connection.getresponse().read()

    assert [seen["path"] for seen in upstream.seen] == ["/a", "/b"]


def test_an_upstream_that_cannot_be_reached_gets_a_502_and_the_request_is_still_listed():
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{unused.getsockname()[1]}"
    capture = Capture(VIA_PROXY, {})

    with logging_proxy(closed, capture) as origin:
        connection = client(origin)
        connection.request("GET", "/models")
        answer = connection.getresponse()
        got = (answer.status, answer.read())

    assert got[0] == 502 and b"couldn't reach" in got[1]
    assert capture.record()["requests"][0]["path"] == "/models"


def test_the_proxy_is_shut_down_when_the_run_ends_even_by_an_error(upstream):
    with pytest.raises(RuntimeError, match="the run failed"), logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        client(origin).request("GET", "/models")  # serving, until the error
        raise RuntimeError("the run failed")

    address = urllib.parse.urlsplit(origin)
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection((address.hostname, address.port), timeout=TIMEOUT)


def raw_upstream(header_lines: bytes) -> str:
    """An upstream that answers one request with `header_lines` as they are, then a 2-byte body; returns its URL."""
    listener = socket.create_server(("127.0.0.1", 0))

    def answer():
        connection, _ = listener.accept()
        with connection, listener:
            connection.recv(65536)
            connection.sendall(b"HTTP/1.1 200 OK\r\n" + header_lines + b"Content-Length: 2\r\n\r\nok")

    threading.Thread(target=answer, daemon=True).start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}"


@pytest.mark.parametrize("fold", [b"\r\n\t", b"\r\n ", b"\n ", b"\r ", b"\r\t"], ids=["crlf-tab", "crlf-space", "lf", "cr", "cr-tab"])
def test_a_header_the_upstream_folds_over_two_lines_reaches_the_harness_on_one(fold):
    upstream = raw_upstream(b"X-Request-Id: r1" + fold + b"Set-Cookie: injected=1\r\n")

    with logging_proxy(upstream, Capture(VIA_PROXY, {})) as origin:
        answer = raw_request(origin, b"GET /models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")

    head = answer.split(b"\r\n\r\n")[0]
    request_ids = [line for line in head.split(b"\r\n") if line.lower().startswith(b"x-request-id:")]
    assert request_ids == [b"X-Request-Id: r1 Set-Cookie: injected=1"]
    assert head.count(b"\r") == head.count(b"\n")  # every CR ends a line: none is left inside a header
