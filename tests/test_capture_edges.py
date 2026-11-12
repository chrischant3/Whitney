"""Edges of capture's reading, the report's section, the proxy's framing and the plumbing (Phase 5 Task 31).

Written after the first mutation run, for behaviour the other capture tests
left unpinned: orders, defaults, counts and wire framing a reader or a
harness would see.
"""

import json
from types import SimpleNamespace

from test_capture_agents import args, harness, run, seen
from test_capture_proxy import Upstream, raw_request
from test_capture_report import log, sent
from test_capture_tasks import evals, task

from whitney import capture, targets
from whitney.agent import RUN_STORE_KEY, kilo, redact
from whitney.capture import VIA_PROXY, Capture
from whitney.capture_proxy import logging_proxy
from whitney.capture_report import render_capture_markdown

KEY = "sk-ant-fake-edges-key-0123456789"


# --- capture ---------------------------------------------------------------------------------


def test_every_model_event_inspect_logged_no_call_for_is_counted():
    events = [SimpleNamespace(event="model", call=None), SimpleNamespace(event="model", call=None), SimpleNamespace(event="tool")]

    assert capture.requests_from_inspect_log(events, {}) == ([], 2)


def test_a_docker_run_finds_a_redacted_text_in_a_request_holding_the_secret():
    events = [SimpleNamespace(event="model", call=SimpleNamespace(request={"system": f"Rule: never print {KEY}.", "messages": []}))]

    found = capture.from_inspect_log(events, {}, {"AGENTS.md": "Rule: never print [redacted]."}, lambda text: redact(text, [KEY]))

    assert found["found"] == {"AGENTS.md": 0}


def test_a_jsonc_file_outside_the_known_config_paths_is_a_config():
    assert capture.source_kind("config/team.jsonc") == "config"


def test_thinking_text_reads_past_lines_that_are_not_events_and_counts_only_the_assistant_s_blocks():
    events = "\n".join([
        "not json",
        "[1, 2]",
        json.dumps({"type": "user", "message": {"content": [{"type": "thinking", "thinking": "not the model's"}]}}),
        json.dumps({"type": "reasoning", "part": {"text": "17 plus 25"}}),
    ])
    user_only = json.dumps({"type": "user", "message": {"content": [{"type": "thinking", "thinking": "not the model's"}]}})

    assert capture.thinking_text(events) == capture.TEXT_IN_EVENTS
    assert capture.thinking_text(user_only) == capture.NO_TEXT


def test_the_score_keeps_how_many_calls_inspect_did_not_log():
    run = {"sent": {"via": "inspect-log", "requests": [{}], "found": {}, "unlogged": 3}}

    assert capture.score_entries(run)["sent"] == {"via": "inspect-log", "requests": 1, "found": {}, "unlogged": 3}


# --- the report's section --------------------------------------------------------------------


def test_the_whole_section_as_it_is_written():
    assert render_capture_markdown([log("template", [sent(**{"AGENTS.md": 0})])]) == (
        "## What was sent to the model\n\n"
        "- `rules.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings "
        "and trailing spaces. template: `AGENTS.md` found in 1 of 1 runs.\n"
    )


def test_variants_are_ordered_by_role_then_by_name_not_by_name_alone():
    report = render_capture_markdown([
        log("aa_control", [sent(**{"AGENTS.md": None})], role="control"),
        log("template", [sent(**{"AGENTS.md": 0})]),
        log("zz_baseline", [sent(**{"AGENTS.md": None})]),
        log("mm_baseline", [sent(**{"AGENTS.md": None})]),
        log("odd", [sent(**{"AGENTS.md": None})], role="something else"),
    ])

    line = report.splitlines()[2]
    order = [line.index(f"{name}:") for name in ("mm_baseline", "zz_baseline", "template", "aa_control", "odd")]
    assert order == sorted(order)


def test_a_log_that_names_no_target_is_a_kilo_log():
    old = log("template", [sent(**{"AGENTS.md": 0})])
    del old.eval.metadata["target"]

    assert "- `rules.json` on kilo: " in render_capture_markdown([old])


def test_every_run_not_captured_is_counted():
    report = render_capture_markdown([log("template", [sent(**{"AGENTS.md": 0}), {}, {}])])

    assert "(2 runs not captured)" in report


def test_a_failed_log_or_an_errored_sample_first_hides_nothing_after_it():
    failed = log("template", [sent(**{"AGENTS.md": 0})], status="error", eval_file="first.json")
    errored = log("template", [sent(**{"AGENTS.md": 0})])
    errored.samples.insert(0, SimpleNamespace(id="e", error="boom", scores={"s": SimpleNamespace(metadata={})}))

    report = render_capture_markdown([failed, errored])

    assert "first.json" not in report
    assert "template: `AGENTS.md` found in 1 of 1 runs." in report


def test_an_agent_s_description_and_body_are_counted_separately():
    report = render_capture_markdown([log("template", [sent(description=0, body=0)], source=".kilo/agents/reviewer.md")])

    assert "agent `.kilo/agents/reviewer.md`, its description and its body looked for separately. template: description found" in report


def test_a_variant_that_had_nothing_to_look_for_says_so():
    report = render_capture_markdown([log("template", [sent()])])

    assert "template: nothing to look for." in report


# --- the proxy's framing ---------------------------------------------------------------------


def test_a_refused_destination_is_told_why():
    upstream = Upstream()
    try:
        with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
            answer = raw_request(origin, b"GET http://evil.invalid/ HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
    finally:
        upstream.server.shutdown()
        upstream.server.server_close()

    assert b"only a path is forwarded" in answer


def test_chunk_sizes_are_read_as_hexadecimal():
    upstream = Upstream()
    body = b'{"messages": [], "pad": "' + b"x" * 13 + b'"}'  # 42 bytes: 2A in hexadecimal
    try:
        with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
            raw_request(origin, (
                b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                b"%X\r\n%s\r\n0\r\n\r\n" % (len(body), body)
            ))
    finally:
        upstream.server.shutdown()
        upstream.server.server_close()

    assert upstream.seen[0]["body"] == body


def test_an_answer_with_a_length_is_passed_on_with_it_unchunked_and_without_its_hop_by_hop_headers():
    upstream = Upstream()
    upstream.reply = {"status": 200, "body": b'{"ok": true}', "headers": {"Keep-Alive": "timeout=5", "content-type": "application/json"}}
    try:
        with logging_proxy(upstream.url, Capture(VIA_PROXY, {})) as origin:
            answer = raw_request(origin, b"POST /v1/messages HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
    finally:
        upstream.server.shutdown()
        upstream.server.server_close()

    head, _, rest = answer.partition(b"\r\n\r\n")
    assert b"content-length: 12" in head.lower()
    assert b"chunked" not in head.lower() and b"keep-alive" not in head.lower()
    assert rest == b'{"ok": true}'


# --- plumbing --------------------------------------------------------------------------------


def test_build_agent_passes_capture_to_claude_code_too():
    from inspect_ai._util.registry import registry_params

    agent = targets.build_agent("claude-code", "anthropic/claude-haiku-4-5", {"claude-code": {}}, capture_prompt=True, capture_texts={"CLAUDE.md": "x"})

    assert registry_params(agent)["capture_prompt"] is True
    assert registry_params(agent)["capture_texts"] == {"CLAUDE.md": "x"}


def test_on_claude_code_the_agent_looks_for_the_instruction_files_agents_md_stands_for(evals, monkeypatch):
    calls = []
    build = targets.build_agent
    monkeypatch.setattr(targets, "build_agent", lambda *args, **kwargs: (calls.append(kwargs), build(*args, **kwargs))[1])
    (evals.project / "CLAUDE.md").write_text("# Claude rules\n")

    task(evals, target="claude-code", model="anthropic/claude-haiku-4-5", capture_prompt=True)

    assert calls[0]["capture_texts"] == {"CLAUDE.md": "# Claude rules\n"}


# --- a Kilo run with a config overlay --------------------------------------------------------


def test_a_kilo_run_with_an_overlay_keeps_auto_thinking_and_its_timeout(harness):
    harness.control(sleep=10)

    run(kilo(model="kilo/anthropic/claude-haiku-4.5", harness_permissions={"bash": "allow"}, auto=True, thinking=True, timeout=2))

    assert seen(harness)["overlay"] == {"permission": {"bash": "allow"}}
    assert args(harness)[6:10] == ["--auto", "--variant", "high", "--thinking"]
    assert harness.stored[RUN_STORE_KEY]["exit_code"] == 124
