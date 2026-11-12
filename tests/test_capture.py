"""What a captured run records: the `sent` entry, the texts looked for, and the thinking level (Phase 5 Task 31).

The request bodies are the ones the Task 31 spike captured from the real
harnesses (tests/data/prompt-capture/, each with a .meta.json naming the
harness version, model, prompt, date and the project files it was sent).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from whitney import capture
from whitney.capture import (
    NO_MODEL_REQUEST,
    NOT_CAPTURED,
    NOT_SET,
    Capture,
    normalised,
    requests_from_inspect_log,
    sent_level,
    source_kind,
    texts_to_find,
    thinking_record,
    thinking_sent,
    thinking_text,
)

CAPTURED = Path(__file__).parent / "data" / "prompt-capture"


def fixture(name: str) -> tuple[bytes, dict]:
    """A captured request's raw body and its meta."""
    return (CAPTURED / f"{name}.request.json").read_bytes(), json.loads((CAPTURED / f"{name}.meta.json").read_text())


def model_body(**fields) -> bytes:
    return json.dumps({"messages": [{"role": "user", "content": "hi"}], **fields}).encode()


# --- normalising -------------------------------------------------------------------------------


def test_normalising_makes_line_endings_plain_and_drops_trailing_spaces_and_outer_blank_lines():
    assert normalised("\n\n# Rules  \r\n\r\n- one\t\r\n- two\n\n") == "# Rules\n\n- one\n- two"


# --- the sent entry ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["kilo-gateway-origin", "kilo-key", "claude-code"])
def test_each_harness_s_captured_request_is_found_to_hold_its_agents_md(name):
    body, meta = fixture(name)
    agents_md = meta["project_files"]["AGENTS.md"]
    found = Capture(capture.VIA_PROXY, {"AGENTS.md": agents_md})

    found.add(body, "POST", meta["request_path"])

    record = found.record()
    assert record["found"] == {"AGENTS.md": 0}
    assert record["first_request"] == 0
    assert record["first_body"] == json.loads(body)
    assert record["requests"] == [{"method": "POST", "path": meta["request_path"], "bytes": len(body), "model_request": True}]
    assert record["via"] == "proxy"


def test_a_skill_s_description_is_found_in_the_first_request_and_its_body_is_not():
    body, meta = fixture("kilo-gateway-origin")
    texts = texts_to_find("kilo", ".kilo/skills/release-notes/SKILL.md", meta["project_files"])
    found = Capture(capture.VIA_PROXY, texts)

    found.add(body, "POST", meta["request_path"])

    assert set(texts) == {"description", "body"}
    assert found.record()["found"] == {"description": 0, "body": None}


def test_a_text_found_in_a_later_request_names_that_request():
    found = Capture(capture.VIA_PROXY, {"body": "Skill body marker: CAPTURE-SKILL-BODY-2287"})

    found.add(b"", "GET", "/api/openrouter/models")
    found.add(model_body(system="s"), "POST", "/m")
    found.add(model_body(system="s", extra=[{"type": "tool_result", "content": "Skill body marker: CAPTURE-SKILL-BODY-2287\r\n"}]), "POST", "/m")

    assert found.record()["found"] == {"body": 2}


def test_requests_that_are_not_model_requests_are_listed_but_not_kept_or_searched():
    found = Capture(capture.VIA_PROXY, {"rule": "rule text"})

    found.add(b'{"rule": "rule text"}', "POST", "/telemetry")
    found.add(b"not json", "POST", "/other")
    found.add(b"[1, 2]", "POST", "/list")

    record = found.record()
    assert record["first_request"] is None and record["first_body"] is None
    assert record["found"] == {"rule": None}
    assert [r["model_request"] for r in record["requests"]] == [False, False, False]
    assert [r["bytes"] for r in record["requests"]] == [21, 8, 6]


def test_later_requests_say_whether_their_system_prompt_and_tools_differ_from_the_first():
    found = Capture(capture.VIA_PROXY, {})

    found.add(model_body(system=[{"text": "a"}], tools=[{"name": "bash"}]), "POST", "/m")
    found.add(model_body(system=[{"text": "a"}], tools=[{"name": "bash"}], more="history"), "POST", "/m")
    found.add(model_body(system=[{"text": "b"}], tools=[{"name": "bash"}]), "POST", "/m")
    found.add(model_body(system=[{"text": "a"}], tools=[]), "POST", "/m")

    later = found.record()["requests"][1:]
    assert [(r["system_differs"], r["tools_differ"]) for r in later] == [(False, False), (True, False), (False, True)]
    assert "system_differs" not in found.record()["requests"][0]


def test_an_openai_format_request_s_system_prompt_is_its_system_messages():
    found = Capture(capture.VIA_PROXY, {})
    first = [{"role": "system", "content": "a"}, {"role": "user", "content": "hi"}]

    found.add(json.dumps({"messages": first}).encode(), "POST", "/c")
    found.add(json.dumps({"messages": [first[0], *first[1:], {"role": "assistant", "content": "x"}]}).encode(), "POST", "/c")
    found.add(json.dumps({"messages": [{"role": "system", "content": "b"}, first[1]]}).encode(), "POST", "/c")

    assert [r["system_differs"] for r in found.record()["requests"][1:]] == [False, True]


def test_an_empty_text_is_never_looked_for():
    found = Capture(capture.VIA_PROXY, {"empty": " \n\r\n", "rule": "a rule"})

    found.add(model_body(system="a rule"), "POST", "/m")

    assert found.record()["found"] == {"rule": 0}


def test_the_record_is_a_copy_so_later_requests_do_not_change_one_already_taken():
    found = Capture(capture.VIA_PROXY, {"rule": "late"})
    found.add(model_body(), "POST", "/m")
    taken = found.record()

    found.add(model_body(system="late"), "POST", "/m")

    assert taken["found"] == {"rule": None} and len(taken["requests"]) == 1


# --- Docker: Inspect's own log ------------------------------------------------------------------


def model_event(request):
    return SimpleNamespace(event="model", call=SimpleNamespace(request=request))


def test_the_bridged_requests_are_read_from_inspect_s_model_events_with_attachments_resolved():
    request = {"messages": [{"role": "user", "content": "attachment://abc"}], "system": [{"text": "attachment://missing"}]}
    events = [SimpleNamespace(event="span_begin"), model_event(request), SimpleNamespace(event="model", call=None), model_event({"messages": []})]

    bodies, unlogged = requests_from_inspect_log(events, {"abc": "the rules"})

    assert [json.loads(body) for body in bodies] == [
        {"messages": [{"role": "user", "content": "the rules"}], "system": [{"text": "attachment://missing"}]},
        {"messages": []},
    ]
    assert unlogged == 1


def test_a_capture_from_inspect_s_log_has_no_method_or_path():
    found = Capture(capture.VIA_INSPECT_LOG, {})

    found.add(model_body())

    assert found.record()["requests"][0] == {"method": None, "path": None, "bytes": len(model_body()), "model_request": True}
    assert found.record()["via"] == "inspect-log"


# --- what is looked for ------------------------------------------------------------------------


@pytest.mark.parametrize(("source", "kind"), [
    ("AGENTS.md", "instructions"),
    (".kilo/rules/style.md", "instructions"),
    (".kilo/skills/release-notes/SKILL.md", "skill"),
    (".claude/skills/release-notes/SKILL.md", "skill"),
    (".claude/agents/reviewer.md", "agent"),
    ("kilo.jsonc", "config"),
    (".kilo/kilo.json", "config"),
    (".claude/settings.json", "config"),
    ("tools/other.json", "config"),
    (None, None),
])  # fmt: skip
def test_each_kind_of_source(source, kind):
    assert source_kind(source) == kind


def test_an_instruction_file_is_looked_for_by_its_own_text():
    assert texts_to_find("kilo", "AGENTS.md", {"AGENTS.md": "rules\n", "app.py": "x"}) == {"AGENTS.md": "rules\n"}


def test_an_instruction_file_that_is_not_staged_gives_nothing_to_look_for():
    assert texts_to_find("kilo", "CLAUDE.md", {"AGENTS.md": "rules\n"}) == {}


def test_on_claude_code_agents_md_stands_for_every_staged_instruction_file_and_what_they_import():
    staged = {
        "CLAUDE.md": "@AGENTS.md\n@docs/rules.md\n",
        "AGENTS.md": "agents rules\n@docs/agents-extra.md\n",
        ".claude/CLAUDE.md": "dot rules\n",
        "docs/rules.md": "@two.md\n",
        "docs/two.md": "two\n",
        "docs/agents-extra.md": "extra\n",
        "app.py": "print()\n",
    }

    assert list(texts_to_find("claude-code", "AGENTS.md", staged)) == [
        "CLAUDE.md", ".claude/CLAUDE.md", "AGENTS.md", "docs/rules.md", "docs/agents-extra.md", "docs/two.md",
    ]


def test_imports_are_followed_four_hops_as_claude_code_follows_them():
    staged = {"CLAUDE.md": "@h1.md\n", "h1.md": "@h2.md\n", "h2.md": "@h3.md\n", "h3.md": "@h4.md\n", "h4.md": "@h5.md\n", "h5.md": "five\n"}

    assert list(texts_to_find("claude-code", "AGENTS.md", staged)) == ["CLAUDE.md", "h1.md", "h2.md", "h3.md", "h4.md"]


def test_an_import_is_matched_to_its_staged_spelling_and_listed_once():
    staged = {"CLAUDE.md": "@agents.md\n@./docs/../AGENTS.md\n", "AGENTS.md": "@CLAUDE.md\n"}

    assert list(texts_to_find("claude-code", "AGENTS.md", staged)) == ["CLAUDE.md", "AGENTS.md"]


def test_on_kilo_agents_md_is_only_agents_md():
    staged = {"CLAUDE.md": "claude\n", "AGENTS.md": "@docs/x.md\n", "docs/x.md": "x\n"}

    assert texts_to_find("kilo", "AGENTS.md", staged) == {"AGENTS.md": "@docs/x.md\n"}


def test_another_instruction_file_on_claude_code_is_only_that_file():
    staged = {"CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "a\n"}

    assert texts_to_find("claude-code", "CLAUDE.md", staged) == {"CLAUDE.md": "@AGENTS.md\n"}


def test_a_skill_is_looked_for_by_its_description_and_its_body():
    skill = "---\nname: notes\ndescription: >\n  Write the notes.\n  Use for notes.\n---\n\n# Notes\n\nThe body.\n"

    assert texts_to_find("kilo", ".kilo/skills/notes/SKILL.md", {".kilo/skills/notes/SKILL.md": skill}) == {
        "description": "Write the notes. Use for notes.",
        "body": "\n# Notes\n\nThe body.\n",
    }


def test_an_agent_without_frontmatter_is_looked_for_by_its_whole_text_as_its_body():
    assert texts_to_find("claude-code", ".claude/agents/r.md", {".claude/agents/r.md": "Review it.\n"}) == {"body": "Review it.\n"}


def test_a_skill_that_is_not_staged_gives_nothing_to_look_for():
    assert texts_to_find("kilo", ".kilo/skills/notes/SKILL.md", {}) == {}


def test_a_permission_file_is_not_looked_for():
    assert texts_to_find("kilo", "kilo.jsonc", {"kilo.jsonc": '{"permission": {}}'}) == {}


def test_a_file_with_no_source_has_nothing_to_look_for():
    assert texts_to_find("kilo", None, {"AGENTS.md": "x"}) == {}


# --- thinking ----------------------------------------------------------------------------------


def test_claude_code_s_own_thinking_setting_is_stated_as_it_was_sent():
    body, _ = fixture("claude-code")

    assert thinking_sent(json.loads(body)) == '"thinking": {"budget_tokens": 31999, "type": "enabled", "display": "updates"}'


def test_kilo_s_setting_on_the_gateway_is_stated_as_it_was_sent():
    body, _ = fixture("kilo-gateway-origin")

    assert thinking_sent(json.loads(body)) == '"thinking": {"type": "disabled"}'


def test_a_request_with_no_thinking_setting_says_so():
    body, _ = fixture("kilo-key")

    assert thinking_sent(json.loads(body)) == "no thinking setting in the request"


def test_an_openai_format_reasoning_setting_is_stated_too():
    assert thinking_sent({"reasoning": {"effort": "high"}, "reasoning_effort": "high"}) == (
        '"reasoning": {"effort": "high"}; "reasoning_effort": "high"'
    )


def test_the_level_sent_is_read_from_the_first_model_request_or_says_why_not():
    assert sent_level(None) == NOT_CAPTURED
    assert sent_level({"first_body": None}) == NO_MODEL_REQUEST
    assert sent_level({"first_body": {"thinking": {"type": "disabled"}}}) == '"thinking": {"type": "disabled"}'


KILO_REASONING = '{"type":"reasoning","part":{"text":"17 plus 25 is 42."}}'
CLAUDE_THINKING = json.dumps({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "Add them.", "signature": "s"}]}})
CLAUDE_WITHHELD = json.dumps({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "", "signature": "s"}]}})
CLAUDE_TEXT = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "42"}]}})


@pytest.mark.parametrize(("events", "said"), [
    (f'{{"type":"step_start"}}\n{KILO_REASONING}\n', "in the events"),
    ('{"type":"reasoning","part":{"text":""}}\n', "none in the events"),
    (f"{CLAUDE_WITHHELD}\n{CLAUDE_TEXT}\n", "withheld: the thinking blocks are empty"),
    (f"{CLAUDE_WITHHELD}\n{CLAUDE_THINKING}\n", "in the events"),
    (f"not json\n[1]\n{CLAUDE_TEXT}\n" + json.dumps({"type": "assistant", "message": {"content": "plain"}}), "none in the events"),
    (json.dumps({"type": "assistant", "message": None}) + '\n{"type":"reasoning","part":null}\n', "none in the events"),
    ("", "none in the events"),
])  # fmt: skip
def test_whether_the_thinking_text_is_in_the_events(events, said):
    assert thinking_text(events) == said


def test_a_local_run_states_what_whitney_set_and_what_the_request_held():
    assert thinking_record("kilo run --variant high --thinking", '"thinking": {"type": "disabled"}', KILO_REASONING, docker=False) == {
        "set": "kilo run --variant high --thinking", "sent": '"thinking": {"type": "disabled"}', "text": "in the events",
    }


def test_a_run_whitney_set_no_thinking_for_says_so():
    assert thinking_record(None, NOT_CAPTURED, "", docker=False) == {"set": NOT_SET, "sent": NOT_CAPTURED, "text": "none in the events"}


def test_a_docker_run_states_what_it_asked_for_and_what_was_applied():
    assert thinking_record("Inspect's reasoning_tokens 31999", "no thinking setting in the request", "", docker=True) == {
        "asked": "Inspect's reasoning_tokens 31999", "applied": "no thinking setting in the request", "text": "none in the events",
    }
