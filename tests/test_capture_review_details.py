"""Details of Task 31's review fixes that the first two rounds of tests left open: what each refusal says, framing at its edges, where a text is found.

Written after the review fixes' second mutation run, as tests/test_capture_review_edges.py
was after the first: each test pins behaviour a harness, a reader or a report relies on.
"""

import gzip
import http.client
import json
import socket
import threading
import urllib.parse
from types import SimpleNamespace

import pytest
from test_capture_proxy import TIMEOUT, Upstream, raw_request
from test_capture_report import log
from test_capture_review import MODEL, claude_code_body, found_score, sent_raw

from whitney import capture, capture_proxy
from whitney.capture import ELSEWHERE, MESSAGES, SYSTEM_PROMPT, VIA_PROXY, Capture, request_text, texts_to_find
from whitney.capture_proxy import MAX_LINE, UPSTREAM_TIMEOUT, _Server, logging_proxy
from whitney.capture_report import render_capture_markdown
from whitney.project_files import without_block_comments

SENT_MODEL = "claude-haiku-4-5"  # MODEL as Claude Code names it in a request


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.release.set()
    server.server.shutdown()
    server.server.server_close()


class CannedUpstream:
    """A fake upstream that reads each request's head and answers it with `answer`, byte for byte, then closes."""

    def __init__(self, answer: bytes) -> None:
        self.answer = answer
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.listener.getsockname()[1]}"
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:  # closed when the test ends
                return
            with connection:
                head = b""
                while b"\r\n\r\n" not in head and (piece := connection.recv(65536)):
                    head += piece
                connection.sendall(self.answer)


def through_canned(answer: bytes, request: bytes) -> bytes:
    """What the proxy hands a client that sends `request` (which should ask to close) when the upstream answers `answer`."""
    canned = CannedUpstream(answer)
    try:
        with logging_proxy(canned.url, Capture(VIA_PROXY, {})) as origin:
            return raw_request(origin, request)
    finally:
        canned.listener.close()


def post(body: bytes, *head: bytes) -> bytes:
    return b"POST /v1/messages HTTP/1.1\r\nHost: x\r\n" + b"".join(head) + b"Connection: close\r\n\r\n" + body


def chunked(*lines: bytes) -> bytes:
    return post(b"".join(lines), b"Transfer-Encoding: chunked\r\n")


# --- what each refusal says ----------------------------------------------------------------------------


@pytest.mark.parametrize(("data", "status_line"), [
    (b"GET http://elsewhere.example/ HTTP/1.1\r\nHost: x\r\n\r\n", b"HTTP/1.1 400 only a path is forwarded\r\n"),
    (post(b"", b"Transfer-Encoding: gzip\r\n"), b"HTTP/1.1 400 only the chunked transfer coding is read\r\n"),
    (post(b"", b"Content-Length: abc\r\n"), b"HTTP/1.1 400 Content-Length must be one whole number\r\n"),
    (chunked(b"ZZ\r\n"), b"HTTP/1.1 400 a chunk size must be hexadecimal\r\n"),
    (chunked(b"2\r\n{}XX0\r\n\r\n"), b"HTTP/1.1 400 a chunk must end with a line end\r\n"),
    (chunked(b"2\r\n{}\r\n"), b"HTTP/1.1 400 a framing line was too long or cut short\r\n"),
    (post(b"{}", b"Content-Length: 10\r\n"), b"HTTP/1.1 400 the body ended before its length\r\n"),
])  # fmt: skip
def test_each_refusal_says_why_in_its_status_line(upstream, data, status_line):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, data)

    assert answer.startswith(status_line), answer[:120]
    assert upstream.seen == []


def test_a_body_over_the_forwarding_limit_is_refused_with_the_limit_named_either_way(upstream, monkeypatch):
    monkeypatch.setattr(capture_proxy, "MAX_FORWARDED_BODY", 10)
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        by_length = sent_raw(origin, post(b"", b"Content-Length: 11\r\n"))
        by_chunks = sent_raw(origin, chunked(b"5\r\n01234\r\n6\r\n567890\r\n0\r\n\r\n"))

    status_line = b"HTTP/1.1 413 a body over 10 bytes isn't forwarded\r\n"
    assert by_length.startswith(status_line) and by_chunks.startswith(status_line)
    assert upstream.seen == []


def test_an_upstream_that_cannot_be_reached_is_named_with_the_error_s_kind():
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        netloc = f"127.0.0.1:{unused.getsockname()[1]}"

    with logging_proxy(f"http://{netloc}", Capture(VIA_PROXY, {})) as origin:
        answer = raw_request(origin, b"GET /models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")

    assert answer.startswith(f"HTTP/1.1 502 Whitney's capture proxy couldn't reach {netloc}: ConnectionRefusedError\r\n".encode())


def test_another_server_error_is_reported_with_the_client_s_address(capsys):
    server = _Server(("127.0.0.1", 0), type("Handler", (), {}))
    try:
        try:
            raise ValueError("a real fault")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 4321))
    finally:
        server.server_close()

    assert "('127.0.0.1', 4321)" in capsys.readouterr().err


# --- framing at its edges ----------------------------------------------------------------------------------


@pytest.mark.parametrize("coding", [b"chunked, ", b", chunked", b"Chunked"])
def test_empty_transfer_coding_list_elements_are_ignored(upstream, coding):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, post(b"2\r\n{}\r\n0\r\n\r\n", b"Transfer-Encoding: " + coding + b"\r\n"))

    assert answer.startswith(b"HTTP/1.1 200")
    assert upstream.seen[0]["body"] == b"{}"


@pytest.mark.parametrize("coding", [b"chunked, gzip", b"chunked, chunked", b" "])
def test_any_other_transfer_coding_list_is_still_refused(upstream, coding):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, post(b"2\r\n{}\r\n0\r\n\r\n", b"Transfer-Encoding: " + coding + b"\r\n"))

    assert answer.startswith(b"HTTP/1.1 400 only the chunked transfer coding is read")
    assert upstream.seen == []


def test_a_chunk_size_may_be_written_in_lowercase_hexadecimal(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, chunked(b"a\r\n0123456789\r\n0\r\n\r\n"))

    assert answer.startswith(b"HTTP/1.1 200")
    assert upstream.seen[0]["body"] == b"0123456789"


def test_a_trailer_is_read_to_its_end_so_the_next_request_on_the_connection_is_read_too(upstream):
    first = b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n0\r\nX-Trailer: 1\r\n\r\n"

    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, first + post(b"[]", b"Content-Length: 2\r\n"))

    assert answer.count(b"HTTP/1.1 200") == 2
    assert [seen["body"] for seen in upstream.seen] == [b"{}", b"[]"]


def test_a_framing_line_one_byte_over_the_limit_is_refused_without_waiting_for_its_end(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        address = urllib.parse.urlsplit(origin)
        with socket.create_connection((address.hostname, address.port), timeout=TIMEOUT) as connection:
            connection.sendall(b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n" + b"1" * (MAX_LINE + 1))
            answer = connection.recv(65536)  # the connection stays open: the proxy must answer on what it has

    assert answer.startswith(b"HTTP/1.1 400 a framing line was too long or cut short")


def test_headers_are_forwarded_but_hop_by_hop_ones_and_the_host_are_not(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        sent_raw(origin, post(b"{}", b"Content-Length: 2\r\nX-Custom: kept\r\nProxy-Authorization: dropped\r\n"))

    headers = upstream.headers()
    assert headers["x-custom"] == ["kept"]
    assert headers["host"] == [urllib.parse.urlsplit(upstream.url).netloc]
    assert "proxy-authorization" not in headers and "accept-encoding" not in headers


def test_an_https_upstream_is_reached_over_https_with_the_upstream_timeout_after_its_path(upstream, monkeypatch):
    made = []

    def https(netloc, **options):
        made.append((netloc, options))
        return http.client.HTTPConnection(netloc, **options)

    monkeypatch.setattr(http.client, "HTTPSConnection", https)
    netloc = urllib.parse.urlsplit(upstream.url).netloc

    with logging_proxy(f"https://{netloc}/v1/", Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, b"POST /messages HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")

    assert answer.startswith(b"HTTP/1.1 200")
    assert made == [(netloc, {"timeout": UPSTREAM_TIMEOUT})]
    assert upstream.seen[0]["path"] == "/v1/messages"


# --- the answer as it is passed back ------------------------------------------------------------------------


def test_the_upstream_s_own_reason_phrase_is_passed_back():
    answer = through_canned(b"HTTP/1.1 200 Fine Thanks\r\nContent-Length: 2\r\n\r\nok", b"GET /models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")

    assert answer == b"HTTP/1.1 200 Fine Thanks\r\nContent-Length: 2\r\n\r\nok"


@pytest.mark.parametrize(("method", "head"), [
    (b"HEAD", b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n"),
    (b"GET", b"HTTP/1.1 204 No Content\r\nX-A: 1\r\n\r\n"),
    (b"GET", b"HTTP/1.1 304 Not Modified\r\nX-A: 1\r\n\r\n"),
])  # fmt: skip
def test_an_answer_that_has_no_body_is_passed_back_as_its_head_alone(method, head):
    answer = through_canned(head, method + b" /models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")

    assert answer == head


def test_an_unsized_answer_is_chunked_again_for_an_http_1_1_client_with_sizes_in_uppercase_hexadecimal():
    answer = through_canned(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + b"z" * 0xAB, b"GET /models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")

    head, _, body = answer.partition(b"\r\n\r\n")
    assert b"\r\nTransfer-Encoding: chunked" in head and b"Connection" not in head
    assert body == b"AB\r\n" + b"z" * 0xAB + b"\r\n0\r\n\r\n"


# --- what a capture reads -------------------------------------------------------------------------------------


def test_a_request_s_text_is_every_string_joined_by_newlines_with_nothing_for_other_values():
    assert request_text({"a": "one", "n": 7, "b": ["two", None]}) == "one\n\ntwo\n"


def test_a_text_spread_over_several_strings_of_a_later_request_is_found_there():
    found = Capture(VIA_PROXY, {"list": "one\n\ntwo", "dict": "three\nfour"}, model=MODEL)

    found.add(claude_code_body("nothing here"), "POST", "/v1/messages")
    later = {"model": SENT_MODEL, "messages": [{"role": "user", "content": ["one", 7, "two"]}], "tools": [{"input": {"a": "three", "b": "four"}}]}
    found.add(json.dumps(later).encode(), "POST", "/v1/messages")

    assert found.record()["found"] == {"list": {"in": "later", "request": 1}, "dict": {"in": "later", "request": 1}}


def test_the_model_is_the_top_level_one_even_after_a_nested_model_key():
    found = Capture(VIA_PROXY, {}, model=MODEL)
    body = {"metadata": {"model": "another-model"}, "model": SENT_MODEL, "messages": [{"role": "user", "content": "hi"}]}

    found.add(json.dumps(body).encode(), "POST", "/v1/messages")

    assert found.record()["first_request"] == 0


def test_messages_that_are_not_a_list_are_not_a_model_call():
    found = Capture(VIA_PROXY, {}, model=MODEL)

    found.add(json.dumps({"model": SENT_MODEL, "messages": "Reply with PONG."}).encode(), "POST", "/v1/messages")

    assert found.record()["first_request"] is None and found.record()["not_model_calls"] == 1


def test_a_cut_body_is_searched_as_cut_even_when_its_kept_start_is_valid_json(monkeypatch):
    body = claude_code_body("the rule\n") + b" " * 50
    monkeypatch.setattr(capture, "MAX_STORED_BODY", len(body) - 10)
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(body[:-10], "POST", "/v1/messages", size=len(body))

    record = found.record()
    assert record["first_body"] is None
    assert record["first_body_cut"] == {"bytes": len(body), "searched_bytes": len(body) - 10}
    assert record["found"]["rule"]["field"] is None


def test_a_cut_call_and_a_whole_one_are_not_compared_in_either_order(monkeypatch):
    whole = claude_code_body("the rule\n")
    long = claude_code_body("the rule\n", pad="z" * 100)
    monkeypatch.setattr(capture, "MAX_STORED_BODY", len(whole))
    cut_first, whole_first = Capture(VIA_PROXY, {}, model=MODEL), Capture(VIA_PROXY, {}, model=MODEL)

    cut_first.add(long[: len(whole)], "POST", "/v1/messages", size=len(long))
    cut_first.add(whole, "POST", "/v1/messages")
    whole_first.add(whole, "POST", "/v1/messages")
    whole_first.add(long[: len(whole)], "POST", "/v1/messages", size=len(long))

    for found in (cut_first, whole_first):
        requests = found.record()["requests"]
        assert [request["model_request"] for request in requests] == [True, True]
        assert "system_differs" not in requests[1]


def test_a_later_call_with_other_tools_says_its_tools_differ():
    found = Capture(VIA_PROXY, {}, model=MODEL)

    found.add(claude_code_body("x", tools=[{"name": "Bash"}]), "POST", "/v1/messages")
    found.add(claude_code_body("x", tools=[{"name": "Read"}]), "POST", "/v1/messages")
    found.add(claude_code_body("x", tools=[{"name": "Bash"}]), "POST", "/v1/messages")

    assert [request.get("tools_differ") for request in found.record()["requests"]] == [None, True, False]


def test_a_gzip_body_that_decodes_to_exactly_the_cap_is_not_cut(monkeypatch):
    body = claude_code_body("the rule\n")
    monkeypatch.setattr(capture, "MAX_STORED_BODY", len(body))
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(gzip.compress(body), "POST", "/v1/messages", encoding="gzip")

    record = found.record()
    assert "cut" not in record["requests"][0]
    assert record["first_body"] == json.loads(body)


# --- where in the first model call a text is ---------------------------------------------------------------------


def where(body: dict, text: str) -> dict:
    found = Capture(VIA_PROXY, {"label": text}, model=MODEL)
    found.add(json.dumps({**body, "model": SENT_MODEL}).encode(), "POST", "/v1/messages")  # the model last: nothing above the rest
    return found.record()["found"]["label"]


def test_a_text_at_the_start_of_the_rest_of_the_request_has_no_heading_from_the_system_prompt_or_messages():
    body = {"system": "A system line", "messages": [{"role": "user", "content": "A message line"}], "tools": [{"description": "the rule"}]}

    assert where(body, "the rule") == {"in": "first", "field": ELSEWHERE, "heading": None}


def test_a_text_held_twice_is_placed_under_the_heading_above_its_first_copy():
    body = {"system": "Heading one\nthe rule\nHeading two\nthe rule", "messages": []}

    assert where(body, "the rule") == {"in": "first", "field": SYSTEM_PROMPT, "heading": "Heading one"}


def test_a_text_that_runs_through_a_role_has_no_heading():
    body = {"system": "Above", "messages": [{"role": "user", "content": "hello"}]}

    assert where(body, "user\nhello") == {"in": "first", "field": MESSAGES, "heading": None}


def test_the_openai_format_s_system_messages_are_the_system_prompt_and_not_messages():
    messages = [{"role": "system", "content": "Rules:\nthe rule"}, "a bare string", {"role": "user", "content": "hello"}]

    assert where({"messages": messages}, "the rule") == {"in": "first", "field": SYSTEM_PROMPT, "heading": "Rules:"}
    spanning = [messages[0], messages[2]]
    assert where({"messages": spanning}, "the rule\nuser\nhello") == {"in": None}  # each field is searched on its own


def test_a_json_body_that_is_not_an_object_is_not_a_model_call():
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(b'["the rule"]', "POST", "/v1/messages")

    assert found.record()["not_model_calls"] == 1 and found.record()["found"]["rule"] == {"in": None, "not_searched": "no model call was captured"}
    assert found.record()["first_body_cut"] is None


def test_a_body_sent_in_many_pieces_keeps_only_the_capture_s_cap(upstream, monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 4)
    found = Capture(VIA_PROXY, {})

    with logging_proxy(upstream.url, found) as origin:
        sent_raw(origin, chunked(b"3\r\nabc\r\n3\r\ndef\r\n3\r\nghi\r\n0\r\n\r\n"))

    assert upstream.seen[0]["body"] == b"abcdefghi"
    assert found.record()["requests"][0]["bytes"] == 9 and found.record()["requests"][0]["cut"] is True


def test_an_upstream_path_is_kept_up_to_its_trailing_slashes_only(upstream):
    with logging_proxy(upstream.url + "/apiX//", Capture(VIA_PROXY, {})) as origin:
        sent_raw(origin, post(b"{}", b"Content-Length: 2\r\n"))

    assert upstream.seen[0]["path"] == "/apiX/v1/messages"


def test_an_event_without_a_kind_is_not_a_model_event():
    assert capture.requests_from_inspect_log([SimpleNamespace()], {}) == ([], 0)


# --- what is looked for -----------------------------------------------------------------------------------------


def test_an_empty_skill_gives_nothing_and_one_with_unclosed_frontmatter_is_all_body():
    source = ".kilo/skills/notes/SKILL.md"
    unclosed = "---\nname: notes\nThe body.\n"

    assert texts_to_find("kilo", source, {source: ""}) == {}
    assert texts_to_find("kilo", source, {source: unclosed}) == {"body": unclosed}


@pytest.mark.parametrize("not_a_closer", ["```X", "``` "])
def test_a_line_that_only_starts_like_a_closing_fence_keeps_the_fence_open(not_a_closer):
    text = f"```\n<!-- kept -->\n{not_a_closer}\n<!-- kept too -->\n```\n<!-- removed -->\nafter\n"

    assert without_block_comments(text) == f"```\n<!-- kept -->\n{not_a_closer}\n<!-- kept too -->\n```\nafter\n"


def test_the_thinking_warnings_say_what_will_and_will_not_be_recorded():
    assert capture.thinking_warnings("kilo", "kilo/anthropic/claude-haiku-4.5", "local", False, True) == [
        "--thinking without --capture-prompt: the harness is told to think, but no request is read, so the level "
        "that reached the model won't be recorded; the log and report give only what Whitney told the harness",
        "--thinking on the kilo target through the Kilo Gateway (a kilo/ model): in real runs on 2026-11-12 (Kilo 7.8.27) "
        "the request still said thinking was disabled, so the model may not think; a provider key "
        "(--api-key-env with an anthropic/ model) did apply it",
    ]


# --- the report -------------------------------------------------------------------------------------------------


FOUND = {"in": "first", "field": SYSTEM_PROMPT, "heading": None}


@pytest.mark.parametrize(("source", "kind"), [(".kilo/skills/notes/SKILL.md", "skill"), (".kilo/agents/reviewer.md", "agent")])
def test_a_skill_or_agent_line_says_its_description_and_body_were_looked_for_apart(source, kind):
    report = render_capture_markdown([log("template", [found_score(description=FOUND)], source=source)])

    assert f"- `rules.json` on kilo: {kind} `{source}`, its description and its body looked for separately. Only what" in report
    assert "  - template: description in the first model call in 1 of 1 runs (system prompt)." in report


def test_another_instruction_file_on_claude_code_is_named_and_matched_without_its_comments():
    report = render_capture_markdown([log("template", [found_score(**{"CLAUDE.md": FOUND})], source="CLAUDE.md", target="claude-code")])

    assert (
        "- `rules.json` on claude-code: instruction file `CLAUDE.md`, looked for as its exact text after removing "
        "block-level HTML comments, as Claude Code does, and normalising line endings and trailing spaces." in report
    )


def test_a_permission_file_is_said_to_be_enforced_not_sent():
    report = render_capture_markdown([log("template", [found_score(**{"AGENTS.md": FOUND})], source="kilo.jsonc")])

    assert "- `rules.json` on kilo: `kilo.jsonc`: not sent as text: enforced by the harness." in report


def test_agents_md_on_claude_code_stands_for_the_staged_instruction_files_but_not_on_kilo():
    runs = [found_score(**{"AGENTS.md": FOUND})]
    logs = [log("template", runs, target="claude-code"), log("template", runs, eval_file="k.json")]

    report = render_capture_markdown(logs)

    assert "- `rules.json` on claude-code: `AGENTS.md` stands for the instruction files staged and the files they import" in report
    assert "- `k.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising" in report


def test_variants_are_listed_by_role_then_by_name():
    runs = [found_score(**{"AGENTS.md": FOUND})]
    logs = [log("aa-template", runs, role="template"), log("zz-baseline", runs), log("mm-baseline", runs)]

    lines = [line for line in render_capture_markdown(logs).splitlines() if line.startswith("  - ")]

    assert [line.split(":")[0] for line in lines] == ["  - mm-baseline", "  - zz-baseline", "  - aa-template"]


def test_a_run_not_searched_is_counted_by_why():
    why = "past 10 bytes of the first model call's 20; its body was cut"
    runs = [found_score(**{"AGENTS.md": {"in": None, "not_searched": why}}), found_score(**{"AGENTS.md": FOUND})]

    report = render_capture_markdown([log("template", runs)])

    assert f"`AGENTS.md` in the first model call in 1 of 2 runs (system prompt), not searched in 1 of 2 runs ({why})." in report
