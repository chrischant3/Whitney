"""Task 31's pre-PR review fixes (2026-11-12): where a text was found, what is kept, and the proxy's framing.

A reviewer ran each of these against the branch: Claude Code's comment
stripping gave a false "not found", a text the agent read later counted as
sent, the first request could be a call that wasn't the run's, the proxy
traced back on malformed framing and stored bodies and query strings whole,
and a compressed body was silently "not a model request".
"""

import gzip
import json
import socket
import subprocess
import urllib.parse
from pathlib import Path

import pytest
from test_capture_agents import KEY as AGENT_KEY
from test_capture_agents import container, model_event, run
from test_capture_proxy import TIMEOUT, Upstream, client, raw_request
from test_capture_report import log
from test_capture_tasks import evals, ran, whitney_run

from whitney import capture
from whitney.agent import RUN_STORE_KEY, kilo
from whitney.capture import VIA_INSPECT_LOG, VIA_PROXY, Capture, texts_to_find
from whitney.capture_proxy import _Server, logging_proxy
from whitney.capture_report import render_capture_markdown
from whitney.project_files import without_block_comments

ROOT = Path(__file__).parent.parent
TEMPLATE = (ROOT / "template" / "claude-code" / "AGENTS.md").read_text()
MODEL = "anthropic/claude-haiku-4-5"


def claude_code_body(instructions: str, model: str = "claude-haiku-4-5", **fields) -> bytes:
    """A request in the shape Claude Code sent (tests/data/prompt-capture/claude-code.request.json), holding `instructions`."""
    reminder = (
        "<system-reminder>\nAs you answer the user's questions, you can use the following context:\n"
        "Contents of /tmp/kilo-eval-1/project/AGENTS.md (project instructions, checked into the codebase):\n\n"
        f"{instructions}</system-reminder>"
    )
    return json.dumps({
        "model": model, "system": [{"type": "text", "text": "You are Claude Code."}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": reminder}, {"type": "text", "text": "Reply with PONG."}]}],
        **fields,
    }).encode()


def kilo_body(instructions: str, model: str = "anthropic/claude-haiku-4.5") -> bytes:
    """A request in the shape Kilo sent on the Gateway (kilo-gateway-origin.request.json), holding `instructions`."""
    return json.dumps({
        "model": model,
        "system": [{"type": "text", "text": "You are Kilo."}, {"type": "text", "text": f"Instructions from: /tmp/k/project/AGENTS.md\n{instructions}"}],
        "messages": [{"role": "user", "content": "Reply with PONG."}],
    }).encode()


# Claude Code's request for the shipped template, as run 2 on 2026-11-12 held it: the text up to
# `## Team rules`, then the reminder's end, with the HTML comment gone.
SENT_TEMPLATE = TEMPLATE.split("<!--")[0].rstrip("\n") + "\n"


# --- 1. Claude Code removes block-level HTML comments --------------------------------------------


def test_the_shipped_claude_code_template_is_found_once_its_html_comment_is_removed_as_claude_code_does():
    texts = texts_to_find("claude-code", "AGENTS.md", {"AGENTS.md": TEMPLATE})
    found = Capture(VIA_PROXY, texts, model=MODEL)

    found.add(claude_code_body(SENT_TEMPLATE), "POST", "/v1/messages?beta=true")

    assert "<!--" in TEMPLATE  # the shipped file still ends with the comment the real run lost
    assert found.record()["found"] == {"AGENTS.md": {
        "in": "first", "field": "messages",
        "heading": "Contents of …/AGENTS.md (project instructions, checked into the codebase):",
    }}


def test_on_kilo_the_comment_is_kept_so_a_request_without_it_does_not_hold_the_file():
    texts = texts_to_find("kilo", "AGENTS.md", {"AGENTS.md": TEMPLATE})
    found = Capture(VIA_PROXY, texts, model="kilo/anthropic/claude-haiku-4.5")

    found.add(kilo_body(SENT_TEMPLATE), "POST", "/api/openrouter/messages")

    assert texts == {"AGENTS.md": TEMPLATE}
    assert found.record()["found"] == {"AGENTS.md": {"in": None}}


def test_kilo_s_template_is_found_whole_with_its_comment_in_the_system_prompt():
    found = Capture(VIA_PROXY, {"AGENTS.md": TEMPLATE}, model="kilo/anthropic/claude-haiku-4.5")

    found.add(kilo_body(TEMPLATE), "POST", "/api/openrouter/messages")

    assert found.record()["found"]["AGENTS.md"] == {"in": "first", "field": "system prompt", "heading": "Instructions from: …/AGENTS.md"}


@pytest.mark.parametrize(("text", "kept"), [
    ("# Rules\n\n<!-- a note -->\n- one\n", "# Rules\n\n- one\n"),
    ("a\n<!-- starts\nstill in it\nends -->\nb\n", "a\nb\n"),
    ("a\n   <!-- three spaces in -->\nb\n", "a\nb\n"),
    ("a\n    <!-- four spaces is code -->\nb\n", "a\n    <!-- four spaces is code -->\nb\n"),
    ("a <!-- inline --> b\n", "a <!-- inline --> b\n"),
    ("```\n<!-- in a fence -->\n```\n<!-- out -->\n", "```\n<!-- in a fence -->\n```\n"),
    ("a\r\n<!-- x -->\r\nb", "a\r\nb"),
])  # fmt: skip
def test_only_block_level_html_comments_outside_fences_are_removed(text, kept):
    assert without_block_comments(text) == kept


def test_on_claude_code_every_instruction_file_and_import_is_looked_for_without_its_comments():
    staged = {"CLAUDE.md": "@docs/x.md\n<!-- note -->\nown\n", "docs/x.md": "x\n<!-- y -->\n"}

    assert texts_to_find("claude-code", "AGENTS.md", staged) == {"CLAUDE.md": "@docs/x.md\nown\n", "docs/x.md": "x\n"}
    assert texts_to_find("claude-code", "CLAUDE.md", staged) == {"CLAUDE.md": "@docs/x.md\nown\n"}


def test_the_report_says_claude_code_s_files_were_matched_after_removing_html_comments():
    first = {"in": "first", "field": "messages", "heading": "Contents of …/AGENTS.md (project instructions, checked into the codebase):"}

    report = render_capture_markdown([log("template", [found_score(**{"AGENTS.md": first})], target="claude-code")])

    assert "exact text after removing block-level HTML comments, as Claude Code does, and normalising line endings and trailing spaces" in report


# --- 2. Where the text was found -----------------------------------------------------------------


def found_score(not_model_calls=0, **found):
    return {"sent": {"via": "proxy", "requests": 2, "not_model_calls": not_model_calls, "found": found}}


def test_a_text_the_agent_read_in_a_later_request_is_not_counted_as_in_the_first():
    found = Capture(VIA_PROXY, {"AGENTS.md": "the rules"}, model=MODEL)
    tool_result = [{"role": "user", "content": [{"type": "tool_result", "content": "the rules\n"}]}]

    found.add(claude_code_body("nothing"), "POST", "/v1/messages")
    found.add(claude_code_body("nothing", extra=tool_result), "POST", "/v1/messages")

    assert found.record()["found"] == {"AGENTS.md": {"in": "later", "request": 1}}


def test_a_text_in_the_first_request_s_messages_names_that_field_and_the_line_above_it():
    found = Capture(VIA_PROXY, {"rule": "Never commit secrets."}, model=MODEL)

    found.add(claude_code_body("# Rules\n\nNever commit secrets.\n"), "POST", "/v1/messages")

    assert found.record()["found"]["rule"] == {"in": "first", "field": "messages", "heading": "# Rules"}


def test_a_text_outside_the_system_prompt_and_messages_is_elsewhere_in_the_request():
    found = Capture(VIA_PROXY, {"rule": "Never commit secrets."}, model=MODEL)

    found.add(claude_code_body("x", tools=[{"name": "bash", "description": "Never commit secrets."}]), "POST", "/v1/messages")

    assert found.record()["found"]["rule"] == {"in": "first", "field": "elsewhere in the request", "heading": "bash"}


def test_a_text_at_the_very_start_of_a_field_has_no_heading():
    found = Capture(VIA_PROXY, {"rule": "You are Claude Code."}, model=MODEL)

    found.add(claude_code_body("x"), "POST", "/v1/messages")

    assert found.record()["found"]["rule"] == {"in": "first", "field": "system prompt", "heading": None}


def test_a_long_heading_is_cut_to_its_first_200_characters():
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(claude_code_body("h" * 500 + "\nthe rule"), "POST", "/v1/messages")

    assert found.record()["found"]["rule"]["heading"] == "h" * 200


def test_the_report_says_where_each_run_found_the_text_and_counts_each_place():
    first = {"in": "first", "field": "system prompt", "heading": "Instructions from: …/AGENTS.md"}
    report = render_capture_markdown([
        log("baseline", [found_score(**{"AGENTS.md": {"in": None}}), found_score(**{"AGENTS.md": {"in": "later", "request": 3}})]),
        log("template", [found_score(1, **{"AGENTS.md": first}), found_score(1, **{"AGENTS.md": first})]),
    ])

    assert report == (
        "## What was sent to the model\n\n"
        "- `rules.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings "
        "and trailing spaces. Only what the first model call held counts as sent as an instruction.\n"
        "  - baseline: `AGENTS.md` not in the first model call in 2 of 2 runs, only in a later request "
        "(for example a file the agent read) in 1.\n"
        "  - template: `AGENTS.md` in the first model call in 2 of 2 runs (system prompt, under `Instructions from: …/AGENTS.md`); "
        "2 other requests weren't the run's model call and weren't searched.\n"
    )


def test_a_variant_found_in_some_runs_and_not_others_counts_both_and_each_place():
    places = [
        {"in": "first", "field": "system prompt", "heading": "H"},
        {"in": "first", "field": "messages", "heading": None},
        {"in": None},
    ]

    report = render_capture_markdown([log("template", [found_score(**{"AGENTS.md": place}) for place in places])])

    assert (
        "  - template: `AGENTS.md` in the first model call in 2 of 3 runs (system prompt, under `H` in 1; messages in 1), "
        "not in the first model call in 1 of 3 runs.\n"
    ) in report


def test_a_run_whose_found_entries_are_not_records_counts_as_not_captured():
    report = render_capture_markdown([log("template", [found_score(**{"AGENTS.md": 0}), found_score(**{"AGENTS.md": {"in": None}})])])

    assert "  - template: `AGENTS.md` not in the first model call in 1 of 1 runs (1 run not captured).\n" in report


# --- 3. --thinking: what is recorded, and the warnings --------------------------------------------


def test_thinking_without_capture_on_a_local_run_is_warned_about_at_the_start(evals, ran, capsys):
    whitney_run(evals, "--thinking")

    err = capsys.readouterr().err
    assert "warning: --thinking without --capture-prompt" in err
    assert "the level that reached the model won't be recorded" in err


def test_thinking_on_the_kilo_gateway_is_warned_about_even_with_capture(evals, ran, capsys):
    whitney_run(evals, "--thinking", "--capture-prompt")

    err = capsys.readouterr().err
    assert "warning: --thinking on the kilo target through the Kilo Gateway" in err
    assert "--thinking without --capture-prompt" not in err
    assert [options["thinking"] for options in ran.tasks] == [True, True]  # warned, not refused


def test_the_warnings_name_only_the_cases_they_are_about():
    assert capture.thinking_warnings("kilo", "kilo/anthropic/claude-haiku-4.5", "docker", False, True) == []
    assert capture.thinking_warnings("kilo", "anthropic/claude-haiku-4-5", "local", True, True) == []
    assert capture.thinking_warnings("claude-code", MODEL, "local", True, True) == []
    assert capture.thinking_warnings("kilo", "kilo/anthropic/claude-haiku-4.5", "local", True, False) == []
    assert len(capture.thinking_warnings("claude-code", MODEL, "local", False, True)) == 1
    assert len(capture.thinking_warnings("kilo", "kilo/anthropic/claude-haiku-4.5", "local", False, True)) == 2


def test_no_warning_without_thinking(evals, ran, capsys):
    whitney_run(evals, "--capture-prompt")

    assert "--thinking" not in capsys.readouterr().err


def test_the_help_says_what_thinking_records_with_and_without_capture(capsys):
    from whitney import cli

    cli.main(["run", "--help"])

    out = " ".join(capsys.readouterr().out.split())
    assert "record the level that reached the model" not in out
    assert "without --capture-prompt a local run records only what Whitney told the harness" in out


def test_a_run_whitney_set_no_level_for_reads_plainly_in_the_report():
    thinking = {"set": capture.NOT_SET, "sent": capture.LEVEL_NOT_RECORDED, "text": "none in the events"}

    report = render_capture_markdown([log("template", [{"thinking": thinking}])])

    assert (
        "- `rules.json` on kilo, thinking: asked for: no thinking level (1 run); "
        "reached the model: not recorded (no --capture-prompt) (1 run); thinking text: none in the events (1 run)."
    ) in report
    assert "set not set" not in report


def test_the_level_that_reached_the_model_is_quoted_from_the_request():
    thinking = {"asked": "x; Inspect's reasoning_tokens 31999", "applied": '"thinking": {"type": "enabled"}', "text": "in the events"}

    report = render_capture_markdown([log("template", [{"thinking": thinking}])])

    assert "asked for: `x; Inspect's reasoning_tokens 31999` (1 run); reached the model: `\"thinking\": {\"type\": \"enabled\"}` (1 run)" in report


def test_a_local_run_without_capture_never_states_a_level_as_reached():
    assert capture.sent_level(None) == "not recorded (no --capture-prompt)"
    assert capture.thinking_record("told", capture.sent_level(None), "", docker=False)["sent"] == capture.LEVEL_NOT_RECORDED


# --- 4. Malformed requests to the proxy ------------------------------------------------------------


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.release.set()
    server.server.shutdown()
    server.server.server_close()


def sent_raw(origin: str, data: bytes) -> bytes:
    """Send `data`, close the sending side, and return everything the proxy answers before closing."""
    address = urllib.parse.urlsplit(origin)
    with socket.create_connection((address.hostname, address.port), timeout=TIMEOUT) as connection:
        connection.sendall(data)
        connection.shutdown(socket.SHUT_WR)
        answer = b""
        while chunk := connection.recv(65536):
            answer += chunk
        return answer


@pytest.mark.parametrize("head", [
    b"Content-Length: abc\r\n",
    b"Content-Length: -1\r\n",
    b"Content-Length: 2\r\nContent-Length: 3\r\n",
    b"Content-Length: 1,1\r\n",
    "Content-Length: \u00b2\r\n".encode("latin-1"),  # a digit to str.isdigit, not to int
    b"Transfer-Encoding: gzip\r\n",
    b"Transfer-Encoding: chunked\r\n\r\nZZ\r\n",
    b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}XX0\r\n",
    b"Transfer-Encoding: chunked\r\n\r\n" + b"1" * 2000 + b"\r\n",
    b"Content-Length: 10\r\n\r\n{}",
    b"Transfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n",
])  # fmt: skip
def test_a_malformed_body_gets_a_400_with_no_traceback_and_nothing_is_forwarded(upstream, head, capfd):
    data = b"POST /v1/messages HTTP/1.1\r\nHost: x\r\n" + head + (b"" if b"\r\n\r\n" in head else b"\r\n")
    found = Capture(VIA_PROXY, {})

    with logging_proxy(upstream.url, found) as origin:
        answer = sent_raw(origin, data)

    assert answer.startswith(b"HTTP/1.1 400"), answer[:80]
    assert upstream.seen == [] and found.record()["requests"] == []
    assert "Traceback" not in capfd.readouterr().err


def test_identical_repeated_lengths_are_one_length(upstream):
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")

    assert answer.startswith(b"HTTP/1.1 200")
    assert upstream.seen[0]["body"] == b"{}"


def test_a_body_over_the_proxy_s_limit_is_refused_unread(upstream, monkeypatch):
    from whitney import capture_proxy

    monkeypatch.setattr(capture_proxy, "MAX_FORWARDED_BODY", 100)
    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = sent_raw(origin, b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: 101\r\n\r\n")
        chunked = sent_raw(origin, b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n65\r\n" + b"x" * 101 + b"\r\n0\r\n\r\n")

    assert answer.startswith(b"HTTP/1.1 413") and chunked.startswith(b"HTTP/1.1 413")
    assert upstream.seen == []


def test_an_http_1_0_client_is_never_sent_chunks(upstream):
    upstream.reply = {"stream": True}
    upstream.release.set()

    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        answer = raw_request(origin, b"POST /v1/messages HTTP/1.0\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")

    head, _, body = answer.partition(b"\r\n\r\n")
    assert b"chunked" not in head.lower()
    assert body == b"event: first\ndata: 1\n\nevent: last\ndata: 2\n\n"  # the body ends when the proxy closes


def test_a_client_gone_before_its_answer_is_not_a_traceback_but_other_errors_still_are(capsys):
    server = _Server(("127.0.0.1", 0), type("Handler", (), {}))
    try:
        for gone in (BrokenPipeError(), ConnectionResetError()):
            try:
                raise gone
            except ConnectionError:
                server.handle_error(None, ("127.0.0.1", 1))
        assert capsys.readouterr().err == ""
        try:
            raise ValueError("a real fault")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1))
        assert "ValueError: a real fault" in capsys.readouterr().err
    finally:
        server.server_close()


# --- 5. What is stored is capped ------------------------------------------------------------------


def test_a_body_over_the_cap_is_forwarded_whole_stored_as_a_marker_and_searched_up_to_the_cut(upstream, monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 2000)
    head = claude_code_body("Early rule.\n")
    body = head[:-1] + b', "pad": "' + b"x" * 5000 + b' Late rule."}'
    found = Capture(VIA_PROXY, {"early": "Early rule.", "late": "Late rule."}, model=MODEL)
    handed: list[int] = []
    add = found.add
    found.add = lambda data, *args, **kwargs: (handed.append(len(data)), add(data, *args, **kwargs))[1]

    with logging_proxy(upstream.url, found) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages", body=body)
        connection.getresponse().read()

    record = found.record()
    assert upstream.seen[0]["body"] == body
    assert handed == [2000]
    assert record["requests"] == [{"method": "POST", "path": "/v1/messages", "bytes": len(body), "model_request": True, "cut": True}]
    assert record["first_body"] is None
    assert record["first_body_cut"] == {"bytes": len(body), "searched_bytes": 2000}
    assert record["found"]["early"]["in"] == "first"
    assert record["found"]["late"] == {"in": None, "not_searched": f"past 2000 bytes of the first model call's {len(body)}; its body was cut"}
    assert capture.sent_level(record) == capture.LEVEL_CUT


def test_a_chunked_body_over_the_cap_is_forwarded_whole_too(upstream, monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 10)
    body = b'{"messages": [], "pad": "' + b"y" * 100 + b'"}'

    with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
        raw_request(origin, b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                    + b"%X\r\n%s\r\n0\r\n\r\n" % (len(body), body))

    assert upstream.seen[0]["body"] == body


def test_the_report_never_says_not_found_for_text_past_a_cut():
    cut = {"in": None, "not_searched": "past 2000 bytes of the first model call's 7000; its body was cut"}

    report = render_capture_markdown([log("template", [found_score(**{"AGENTS.md": cut})])])

    assert "  - template: `AGENTS.md` not searched in 1 of 1 runs (past 2000 bytes of the first model call's 7000; its body was cut).\n" in report
    assert "not in the first model call" not in report


def test_the_default_cap_is_16_mib():
    assert capture.MAX_STORED_BODY == 16 * 1024 * 1024


# --- 6. The first request is the run's model call ---------------------------------------------------


def test_calls_that_are_not_the_run_s_model_call_never_set_the_first_request():
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(claude_code_body("the rule", model="claude-3-5-haiku"), "POST", "/v1/messages")  # a background call
    found.add(claude_code_body("the rule"), "POST", "/v1/messages/count_tokens")
    found.add(json.dumps({"messages": [], "system": "the rule"}).encode(), "POST", "/v1/messages")  # names no model
    found.add(claude_code_body("x", thinking={"type": "enabled"}), "POST", "/v1/messages?beta=true")

    record = found.record()
    assert record["first_request"] == 3
    assert record["not_model_calls"] == 3
    assert record["found"] == {"rule": {"in": None}}
    assert [r["model_request"] for r in record["requests"]] == [False, False, False, True]
    assert capture.sent_level(record) == '"thinking": {"type": "enabled"}'


def test_a_later_model_call_s_differs_flags_compare_it_with_the_run_s_first_model_call():
    found = Capture(VIA_PROXY, {}, model="kilo/anthropic/claude-haiku-4.5")

    found.add(kilo_body("rules", model="anthropic/claude-haiku-3"), "POST", "/api/openrouter/messages")
    found.add(kilo_body("rules"), "POST", "/api/openrouter/messages")
    found.add(kilo_body("rules"), "POST", "/api/openrouter/chat/completions")

    later = found.record()["requests"][2]
    assert (later["system_differs"], later["tools_differ"]) == (False, False)
    assert "system_differs" not in found.record()["requests"][0]


def test_the_model_is_matched_by_the_name_the_harness_sends():
    for model, sent in [("kilo/anthropic/claude-haiku-4.5", "anthropic/claude-haiku-4.5"), (MODEL, "claude-haiku-4-5"), (MODEL, MODEL)]:
        found = Capture(VIA_PROXY, {}, model=model)
        found.add(claude_code_body("x", model=sent), "POST", "/v1/messages")
        assert found.record()["first_request"] == 0, (model, sent)


def test_inspect_s_log_is_matched_by_its_order_alone_since_the_bridge_answers_every_call_with_the_run_s_model():
    found = capture.from_inspect_log([model_event({"model": "anything", "messages": []})], {}, {})

    assert found["first_request"] == 0


def test_the_report_counts_the_requests_that_were_not_the_run_s_model_call():
    report = render_capture_markdown([log("template", [found_score(1, **{"AGENTS.md": {"in": None}}), found_score(2, **{"AGENTS.md": {"in": None}})])])

    assert "3 other requests weren't the run's model call and weren't searched" in report


# --- 7. Kilo in Docker is redacted --------------------------------------------------------------------


def test_a_docker_kilo_run_s_sent_is_redacted_as_claude_code_s_is(container):
    container.bridged.append(model_event({"model": "x", "messages": [{"role": "user", "content": f"key {AGENT_KEY}"}], "note": AGENT_KEY}))

    run(kilo(model="kilo/anthropic/claude-haiku-4.5", execution="docker", capture_prompt=True))

    stored = container.stored[RUN_STORE_KEY]
    assert AGENT_KEY not in json.dumps(stored)
    assert stored["sent"]["first_body"]["note"] == "[redacted]"


# --- 8. The stored path has no query -----------------------------------------------------------------


def test_the_stored_path_drops_its_query_and_the_upstream_still_gets_it(upstream):
    found = Capture(VIA_PROXY, {})

    with logging_proxy(upstream.url, found) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages?beta=true&api_key=sk-secret-123", body=b"{}")
        connection.getresponse().read()

    assert upstream.seen[0]["path"] == "/v1/messages?beta=true&api_key=sk-secret-123"
    assert found.record()["requests"] == [{"method": "POST", "path": "/v1/messages", "bytes": 2, "model_request": False, "query_removed": True}]
    assert "sk-secret" not in json.dumps(found.record())


# --- 9. Compressed bodies ------------------------------------------------------------------------------


def test_a_gzip_body_is_decoded_and_searched_and_forwarded_as_it_came(upstream):
    body = gzip.compress(claude_code_body("the rule\n"))
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    with logging_proxy(upstream.url, found) as origin:
        connection = client(origin)
        connection.request("POST", "/v1/messages", body=body, headers={"content-encoding": "gzip"})
        connection.getresponse().read()

    record = found.record()
    assert upstream.seen[0]["body"] == body
    assert record["found"]["rule"]["in"] == "first"
    assert record["requests"][0] == {"method": "POST", "path": "/v1/messages", "bytes": len(body), "model_request": True, "encoding": "gzip"}


def test_a_gzip_body_that_decodes_past_the_cap_is_cut(monkeypatch):
    monkeypatch.setattr(capture, "MAX_STORED_BODY", 1000)
    body = claude_code_body("the rule\n", pad="z" * 5000)
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(gzip.compress(body), "POST", "/v1/messages", encoding="gzip")

    assert found.record()["requests"][0]["cut"] is True
    assert found.record()["found"]["rule"]["in"] == "first"


@pytest.mark.parametrize(("encoding", "body", "why"), [
    ("br", b"\x8b\x00", "compressed (br), not searched"),
    ("gzip", b"not gzip at all", "compressed (gzip), could not be decoded"),
])  # fmt: skip
def test_a_body_that_cannot_be_decoded_is_recorded_as_not_searched(encoding, body, why):
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(body, "POST", "/v1/messages", encoding=encoding)

    record = found.record()
    assert record["requests"][0] == {
        "method": "POST", "path": "/v1/messages", "bytes": len(body), "model_request": False, "encoding": encoding, "not_searched": why,
    }
    assert record["not_searched"] == 1
    assert record["found"]["rule"] == {"in": None, "not_searched": "no model call was captured; 1 compressed request wasn't searched"}


def test_identity_encoding_is_read_as_plain():
    found = Capture(VIA_PROXY, {"rule": "the rule"}, model=MODEL)

    found.add(claude_code_body("the rule"), "POST", "/v1/messages", encoding=" Identity ")

    assert found.record()["found"]["rule"]["in"] == "first"
    assert "encoding" not in found.record()["requests"][0]


def test_the_report_says_a_compressed_body_was_not_searched_rather_than_not_found():
    unsearched = {"in": None, "not_searched": "no model call was captured; 2 compressed requests weren't searched"}
    score = {"sent": {"via": "proxy", "requests": 2, "not_model_calls": 0, "not_searched": 2, "found": {"AGENTS.md": unsearched}}}

    report = render_capture_markdown([log("template", [score])])

    assert (
        "  - template: `AGENTS.md` not searched in 1 of 1 runs (no model call was captured; 2 compressed requests weren't searched).\n"
    ) in report


def test_a_run_with_no_model_call_says_so():
    found = Capture(VIA_PROXY, {"rule": "x"}, model=MODEL)

    found.add(b"", "HEAD", "/api/hello")

    assert found.record()["found"]["rule"] == {"in": None, "not_searched": "no model call was captured"}


def test_the_score_keeps_the_counts_the_report_needs():
    sent = Capture(VIA_INSPECT_LOG, {"rule": "x"}).record() | {"not_model_calls": 2, "not_searched": 1, "unlogged": 4}

    assert capture.score_entries({"sent": sent})["sent"] == {
        "via": "inspect-log", "requests": 0, "not_model_calls": 2, "not_searched": 1, "unlogged": 4,
        "found": {"rule": {"in": None, "not_searched": "no model call was captured"}},
    }


# --- 12. Verdict files ----------------------------------------------------------------------------------


def test_an_independent_verifier_s_verdict_files_are_never_committed():
    """Asked of git from the checkout's root, so it holds wherever the tests run from (mutmut copies them)."""
    here = Path(__file__).parent
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here, capture_output=True, text=True, check=True).stdout.strip()

    ignored = subprocess.run(["git", "check-ignore", "--no-index", "-q", ".verifier/verdict.json"], cwd=root, check=False)

    assert ignored.returncode == 0


def test_a_watch_carries_the_run_s_model_so_a_capture_knows_which_call_is_the_run_s():
    assert capture.watch_for(True, False, {"a": "b"}, "told", model=MODEL) == capture.Watch(True, {"a": "b"}, None, MODEL)
