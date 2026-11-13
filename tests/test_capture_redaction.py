"""redact_run covers every field of a run record, not just its events and stderr (Phase 5 Task 31).

The Task 31 spike put a fake key in a captured request's tool result and kept
the request in a field of its own: it survived redact_run, which redacted
only `events` and `stderr` (docs/spike-findings.md, "Phase 5 Task 31",
"Secrets"). A captured run keeps the request under `sent`, so the whole
record has to be redacted.
"""

from whitney.agent import REDACTED, redact_run

KEY = "sk-ant-fake-key-0123456789"


def test_a_key_in_a_separate_field_is_redacted():
    run = {"exit_code": 0, "events": "", "stderr": "", "sent": {"note": f"key {KEY}"}}

    assert redact_run(run, [KEY])["sent"] == {"note": f"key {REDACTED}"}


def test_a_key_inside_a_captured_tool_result_is_redacted_however_deep():
    tool_result = {"type": "tool_result", "content": [{"type": "text", "text": f"ANTHROPIC_API_KEY={KEY}\n"}]}
    body = {"messages": [{"role": "user", "content": [tool_result]}], "system": [{"text": "rules"}]}
    run = {"exit_code": 0, "events": "", "stderr": "", "sent": {"first_body": body, "requests": [{"path": f"/v1?k={KEY}"}]}}

    redacted = redact_run(run, [KEY])

    assert KEY not in repr(redacted)
    assert redacted["sent"]["first_body"]["messages"][0]["content"][0]["content"][0]["text"] == f"ANTHROPIC_API_KEY={REDACTED}\n"
    assert redacted["sent"]["requests"][0]["path"] == f"/v1?k={REDACTED}"
    assert redacted["sent"]["first_body"]["system"] == [{"text": "rules"}]


def test_a_key_used_as_a_json_name_is_redacted_too():
    run = {"exit_code": 0, "events": "", "stderr": "", "sent": {KEY: 1}}

    assert redact_run(run, [KEY])["sent"] == {REDACTED: 1}


def test_numbers_booleans_and_none_are_kept_as_they_are():
    run = {"exit_code": 124, "events": f"a {KEY}", "stderr": KEY, "ended_early": True, "final_text": None, "found": [0, None]}

    assert redact_run(run, [KEY]) == {
        "exit_code": 124, "events": f"a {REDACTED}", "stderr": REDACTED, "ended_early": True, "final_text": None, "found": [0, None],
    }


def test_the_run_given_is_not_changed():
    sent = {"first_body": {"text": KEY}}
    run = {"exit_code": 0, "events": KEY, "stderr": "", "sent": sent}

    redact_run(run, [KEY])

    assert run == {"exit_code": 0, "events": KEY, "stderr": "", "sent": {"first_body": {"text": KEY}}}
