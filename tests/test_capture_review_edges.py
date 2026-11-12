"""Edges of Task 31's review fixes that a reader or a harness would see: limits at their boundary, cut bodies, plurals.

Written after the review fixes' first mutation run, as tests/test_capture_edges.py
was after the first build's.
"""

import json

import pytest
from test_capture_proxy import Upstream, client, raw_request
from test_capture_report import log
from test_capture_review import MODEL, claude_code_body, found_score, sent_raw

from whitney import capture, capture_proxy
from whitney.capture import VIA_PROXY, Capture
from whitney.capture_proxy import MAX_LINE, logging_proxy
from whitney.capture_report import render_capture_markdown


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.release.set()
    server.server.shutdown()
    server.server.server_close()


def chunked(*lines: bytes) -> bytes:
    return b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n" + b"".join(lines)


# --- the proxy's limits ------------------------------------------------------------------------------


def test_a_framing_line_of_exactly_the_limit_is_read_and_one_byte_more_is_refused(upstream):
    extension = b"x" * (MAX_LINE - len(b"2;\r\n"))
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        fits = sent_raw(origin, chunked(b"2;" + extension + b"\r\n{}\r\n0\r\n\r\n"))
        over = sent_raw(origin, chunked(b"2;" + extension + b"x\r\n{}\r\n0\r\n\r\n"))

    assert fits.startswith(b"HTTP/1.1 200") and over.startswith(b"HTTP/1.1 400")
    assert [seen["body"] for seen in upstream.seen] == [b"{}"]


def test_a_chunk_size_has_at_most_16_hexadecimal_digits(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        sixteen = sent_raw(origin, chunked(b"0" * 15 + b"2\r\n{}\r\n0\r\n\r\n"))
        seventeen = sent_raw(origin, chunked(b"0" * 16 + b"2\r\n{}\r\n0\r\n\r\n"))

    assert sixteen.startswith(b"HTTP/1.1 200") and seventeen.startswith(b"HTTP/1.1 400")


def test_a_body_of_exactly_the_forwarding_limit_is_forwarded_either_way(upstream, monkeypatch):
    monkeypatch.setattr(capture_proxy, "MAX_FORWARDED_BODY", 10)
    body = b"0123456789"
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        by_length = sent_raw(origin, b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: 10\r\nConnection: close\r\n\r\n" + body)
        by_chunks = sent_raw(origin, chunked(b"5\r\n01234\r\n5\r\n56789\r\n0\r\n\r\n"))
        past = sent_raw(origin, chunked(b"5\r\n01234\r\n6\r\n56789a\r\n0\r\n\r\n"))

    assert by_length.startswith(b"HTTP/1.1 200") and by_chunks.startswith(b"HTTP/1.1 200")
    assert past.startswith(b"HTTP/1.1 413")
    assert [seen["body"] for seen in upstream.seen] == [body, body]


def test_an_http_1_0_client_that_asks_to_keep_alive_still_has_an_unsized_answer_ended_by_closing(upstream):
    upstream.reply = {"stream": True}
    upstream.release.set()

    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = raw_request(origin, b"POST /v1/messages HTTP/1.0\r\nHost: x\r\nConnection: keep-alive\r\nContent-Length: 2\r\n\r\n{}")

    head, _, body = answer.partition(b"\r\n\r\n")
    assert b"connection: close" in head.lower() and b"chunked" not in head.lower()
    assert body.endswith(b"event: last\ndata: 2\n\n")


def test_a_sized_answer_to_an_http_1_0_client_keeps_its_length(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = raw_request(origin, b"GET /models HTTP/1.0\r\nHost: x\r\n\r\n")

    head, _, body = answer.partition(b"\r\n\r\n")
    assert b"content-length: 12" in head.lower() and b"connection: close" not in head.lower()
    assert body == b'{"ok": true}'


def test_the_capture_is_handed_exactly_its_cap_of_a_longer_body_sent_in_pieces(upstream, monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 7)
    handed = []
    found = Capture(VIA_PROXY, {})
    found.add = lambda data, *args, **kwargs: handed.append((bytes(data), kwargs["size"]))

    with logging_proxy(upstream.url, found) as origin:
        sent_raw(origin, chunked(b"5\r\n01234\r\n5\r\n56789\r\n0\r\n\r\n"))

    assert handed == [(b"0123456", 10)]


# --- what the capture reads ---------------------------------------------------------------------------


def test_a_body_of_exactly_the_cap_is_not_cut(monkeypatch):
    body = claude_code_body("the rule\n")
    monkeypatch.setattr(capture, "MAX_STORED_BODY", len(body))
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(body, "POST", "/v1/messages")

    assert "cut" not in found.record()["requests"][0]
    assert found.record()["first_body"] == json.loads(body)


def test_a_cut_body_is_searched_past_strings_it_cannot_read(monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 120)
    body = b'{"model": "claude-haiku-4-5", "messages": [], "bad": "\\q", "odd": "\xff", "rule": "the rule", "pad": "' + b"z" * 500 + b'"}'
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(body, "POST", "/v1/messages")

    assert found.record()["found"]["rule"] == {"in": "first", "field": None, "heading": "rule"}
    assert found.record()["first_body_cut"] == {"bytes": len(body), "searched_bytes": 120}


@pytest.mark.parametrize("start", [
    b'{"messages": [], "pad": "',  # no model before the cut
    b'{"model": "\\q", "messages": [], "pad": "',  # a model json can't read
    b'{"model": "claude-haiku-4-5", "pad": "',  # no messages before the cut
])  # fmt: skip
def test_a_cut_body_that_does_not_show_the_run_s_model_call_before_the_cut_is_not_taken_for_it(monkeypatch, start):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 60)
    found = Capture(VIA_PROXY, {}, model=MODEL)

    found.add(start + b"z" * 500 + b'"}', "POST", "/v1/messages")

    assert found.record()["first_request"] is None and found.record()["not_model_calls"] == 1


def test_a_cut_later_call_is_searched_and_a_cut_first_call_leaves_later_ones_unflagged(monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 200)
    big = claude_code_body("x", pad="z" * 500)
    found = Capture(VIA_PROXY, {"rule": "Later rule."}, model=MODEL)

    found.add(big, "POST", "/v1/messages")
    found.add(claude_code_body("x"), "POST", "/v1/messages")
    found.add(b'{"model": "claude-haiku-4-5", "messages": [], "note": "Later rule.", "pad": "' + b"z" * 500 + b'"}', "POST", "/v1/messages")

    record = found.record()
    assert "system_differs" not in record["requests"][1]
    assert record["found"]["rule"]["in"] == "later" and record["found"]["rule"]["request"] == 2


def test_a_text_in_several_later_requests_names_the_first_of_them_and_one_in_the_first_call_stays_first():
    found = Capture(VIA_PROXY, {"late": "Late rule.", "early": "Early rule."}, model=MODEL)

    found.add(claude_code_body("Early rule."), "POST", "/v1/messages")
    found.add(claude_code_body("Early rule. Late rule."), "POST", "/v1/messages")
    found.add(claude_code_body("Late rule."), "POST", "/v1/messages")

    assert found.record()["found"]["late"] == {"in": "later", "request": 1}
    assert found.record()["found"]["early"]["in"] == "first"


def test_two_compressed_requests_are_counted_in_why_nothing_was_searched():
    found = Capture(VIA_PROXY, {"rule": "x"}, model=MODEL)

    found.add(b"\x00", "POST", "/v1/messages", encoding="br")
    found.add(b"\x00", "POST", "/v1/messages", encoding="zstd")

    assert found.record()["found"]["rule"]["not_searched"] == "no model call was captured; 2 compressed requests weren't searched"


def test_a_gzip_body_whose_compressed_bytes_were_cut_is_cut():
    import gzip
    import os

    body = gzip.compress(claude_code_body("the rule\n", pad=os.urandom(64).hex()))
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(body[: len(body) // 2], "POST", "/v1/messages", size=len(body), encoding="gzip")

    assert found.record()["requests"][0]["cut"] is True


# --- the report -----------------------------------------------------------------------------------------


def test_one_other_request_is_named_in_the_singular():
    report = render_capture_markdown([log("template", [found_score(1, **{"AGENTS.md": {"in": None}})])])

    assert "; 1 other request wasn't the run's model call and wasn't searched." in report


def test_a_text_found_in_a_cut_first_call_says_it_was_in_what_was_read():
    place = {"in": "first", "field": None, "heading": "# Rules"}

    report = render_capture_markdown([log("template", [found_score(**{"AGENTS.md": place})])])

    assert "(in what was read before the cut, under `# Rules`)" in report


def test_a_thinking_field_the_report_has_no_name_for_is_shown_by_its_own():
    report = render_capture_markdown([log("template", [{"thinking": {"other": "v"}}])])

    assert "thinking: other: `v` (1 run)." in report


def test_a_proxy_request_with_no_body_names_no_encoding(upstream):
    found = Capture(VIA_PROXY, {})

    with logging_proxy(upstream.url, found) as origin:
        connection = client(origin)
        connection.request("GET", "/models")
        connection.getresponse().read()

    assert found.record()["requests"] == [{"method": "GET", "path": "/models", "bytes": 0, "model_request": False}]
