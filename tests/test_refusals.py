"""Refusals an eval file asked for: a skill or an agent its own harness_permissions switched off.

Every stream is a recorded one: Claude Code 2.1.322 and Kilo 7.8.27, from the
real runs of 2026-11-11 (docs/spike-findings.md, "Phase 5 Task 36b"), and
Kilo 7.8.18's `task-deny`, whose rule was the project's.
"""

import json
from pathlib import Path

import pytest

from whitney.refusals import Rule, asked, expected, refused

DATA = Path(__file__).parent / "data"
BLOCKED = "Skill execution blocked by permission rules"


def run(target: str, name: str) -> dict:
    """The fixture's run record, as the agent stores it: its events and, for Claude Code, its denials."""
    folder = DATA / ("claude-code-events" if target == "claude-code" else "kilo-events")
    meta = json.loads((folder / f"{name}.meta.json").read_text())
    return {"events": (folder / f"{name}.jsonl").read_text(), "permission_denials": meta.get("permission_denials") or []}


def rules(target: str, name: str) -> list[Rule]:
    folder = DATA / ("claude-code-events" if target == "claude-code" else "kilo-events")
    return asked(target, json.loads((folder / f"{name}.meta.json").read_text())["harness_permissions"])


# --- Which rules an eval file's permissions ask for ------------------------------------------


def test_claude_codes_named_skill_and_agent_deny_rules_are_the_rules_asked_for():
    permissions = {
        "allow": ["Bash", "Skill(debugging)"],
        "deny": ["Skill(code-review)", "Agent(general-purpose)", "Bash", "Bash(git push *)", "Skill", "Skill(review *)", "Agent()", 7],
    }

    assert asked("claude-code", permissions) == [
        Rule("skill", "code-review", "Skill(code-review)"),
        Rule("agent", "general-purpose", "Agent(general-purpose)"),
    ]


def test_kilos_skill_and_task_deny_patterns_are_the_rules_asked_for():
    permissions = {
        "bash": {"*": "allow", "git push *": "deny"},
        "skill": {"*": "allow", "kilo-config": "deny", "debugging": "ask"},
        "task": {"general": "deny"},
        "edit": "deny",
    }

    assert asked("kilo", permissions) == [Rule("skill", "kilo-config", "skill: kilo-config"), Rule("agent", "general", "task: general")]


@pytest.mark.parametrize("permissions", [{}, {"skill": "deny", "task": "deny"}, {"skill": ["kilo-config"]}])
def test_a_kilo_permission_that_removes_the_tool_or_isnt_a_set_of_patterns_asks_for_no_refusal(permissions):
    """A scalar deny removes the tool, so Kilo prints no refusal to count (docs/spike-findings.md, Task 22)."""
    assert asked("kilo", permissions) == []


@pytest.mark.parametrize("permissions", [{}, {"allow": ["Bash"]}, {"deny": "Skill(code-review)"}, {"deny": None}])
def test_claude_code_permissions_without_a_list_of_deny_rules_ask_for_none(permissions):
    assert asked("claude-code", permissions) == []


# --- What each rule refused in a run -------------------------------------------------------


@pytest.mark.parametrize("name, rule, calls", [
    ("skill-denied-builtin", "Skill(code-review)", 2),
    ("skill-denied", "Skill(release-notes)", 1),
    ("skill-denied-in-subagent", "Skill(release-notes)", 1),
])
def test_a_refused_skill_call_on_claude_code_is_counted_with_claude_codes_words(name, rule, calls):
    assert refused("claude-code", rules("claude-code", name), run("claude-code", name)) == [
        {"rule": rule, "calls": calls, "said": BLOCKED},
    ]


@pytest.mark.parametrize("name, agent", [("agent-denied-builtin", "general-purpose"), ("agent-denied", "reviewer")])
def test_a_refused_agent_call_on_claude_code_is_counted_though_it_is_no_permission_denial(name, agent):
    record = run("claude-code", name)

    assert record["permission_denials"] == []
    assert refused("claude-code", rules("claude-code", name), record) == [{
        "rule": f"Agent({agent})",
        "calls": 1,
        "said": f"Agent type '{agent}' has been denied by permission rule 'Agent({agent})' from flagSettings.",
    }]


def test_a_rule_that_refused_nothing_is_listed_with_no_calls():
    asked_for = [Rule("skill", "release-notes", "Skill(release-notes)"), Rule("skill", "code-review", "Skill(code-review)"),
                 Rule("agent", "reviewer", "Agent(reviewer)")]

    assert refused("claude-code", asked_for, run("claude-code", "skill-denied")) == [
        {"rule": "Skill(release-notes)", "calls": 1, "said": BLOCKED},
        {"rule": "Skill(code-review)", "calls": 0, "said": None},
        {"rule": "Agent(reviewer)", "calls": 0, "said": None},
    ]


def test_a_skill_call_that_failed_for_another_reason_is_no_refusal():
    """skill-missing: the skill doesn't exist, so the call's result is an error, and nothing denied it."""
    record = run("claude-code", "skill-missing")
    called = next(
        block["input"]["skill"] for line in record["events"].splitlines() if '"tool_use"' in line
        for block in json.loads(line)["message"]["content"] if block.get("name") == "Skill"
    )

    assert refused("claude-code", [Rule("skill", called, f"Skill({called})")], record) == [
        {"rule": f"Skill({called})", "calls": 0, "said": None},
    ]


def test_an_agent_call_that_failed_for_another_reason_is_no_refusal():
    """agent-missing: "Agent type '...' not found" names no permission rule."""
    record = run("claude-code", "agent-missing")
    called = next(
        block["input"]["subagent_type"] for line in record["events"].splitlines() if '"tool_use"' in line
        for block in json.loads(line)["message"]["content"] if block.get("name") == "Agent"
    )

    assert refused("claude-code", [Rule("agent", called, f"Agent({called})")], record)[0]["calls"] == 0


def test_a_skill_or_agent_that_ran_is_no_refusal():
    assert refused("claude-code", [Rule("skill", "release-notes", "Skill(release-notes)")], run("claude-code", "skill-named"))[0]["calls"] == 0
    assert refused("kilo", [Rule("skill", "kilo-config", "skill: kilo-config")], run("kilo", "skill-builtin-loaded"))[0]["calls"] == 0


@pytest.mark.parametrize("name, rule, permission", [
    ("skill-denied-overlay", "skill: kilo-config", "skill"),
    ("task-denied-overlay", "task: general", "task"),
])
def test_a_call_kilo_refused_under_the_eval_files_rule_is_counted_with_kilos_words(name, rule, permission):
    found = refused("kilo", rules("kilo", name), run("kilo", name))

    assert [(item["rule"], item["calls"]) for item in found] == [(rule, 1)]
    assert found[0]["said"].startswith("The user has specified a rule which prevents you from using this specific tool call.")
    assert f'"permission":"{permission}"' in found[0]["said"]


def test_a_call_kilo_refused_under_the_projects_own_rule_is_not_the_eval_files_refusal():
    """task-deny: the project's kilo.jsonc denied q7-helper, and Kilo names the rule's source as the project."""
    assert refused("kilo", [Rule("agent", "q7-helper", "task: q7-helper")], run("kilo", "task-deny")) == [
        {"rule": "task: q7-helper", "calls": 0, "said": None},
    ]


@pytest.mark.parametrize("events", ["", "not json\n[1]\n", '{"type": "assistant"}\n{"type": "tool_use", "part": "x"}\n'])
@pytest.mark.parametrize("target", ["claude-code", "kilo"])
def test_a_stream_that_cant_be_read_shows_no_refusal(target, events):
    rule = Rule("skill", "release-notes", "any")

    assert refused(target, [rule], {"events": events}) == [{"rule": "any", "calls": 0, "said": None}]


# --- Which Claude Code denials the eval file expected ----------------------------------------


def test_a_denied_skill_the_eval_file_named_is_expected_and_any_other_denial_is_not():
    named = [Rule("skill", "release-notes", "Skill(release-notes)"), Rule("agent", "reviewer", "Agent(reviewer)")]
    skill = run("claude-code", "skill-denied")["permission_denials"][0]

    assert expected(named, skill) is True
    assert expected([], skill) is False
    assert expected([Rule("skill", "code-review", "Skill(code-review)")], skill) is False
    assert expected([Rule("agent", "release-notes", "Agent(release-notes)")], skill) is False
    assert expected(named, {"tool_name": "Bash", "tool_use_id": "toolu_1", "tool_input": {"command": "release-notes"}}) is False
    assert expected(named, {"tool_name": "Skill", "tool_input": "release-notes"}) is False
    assert expected(named, {"tool_name": "Skill"}) is False
