"""The checks skill_loaded, skill_not_loaded, subagent_delegated and subagent_not_delegated.

They are judged on the event streams Kilo 7.8.18 really printed, saved by
plan Task 16 under tests/data/kilo-events/ (docs/spike-findings.md, "Phase 5:
confirmed fields"), and on those streams changed by hand to show what a
different Kilo output would do.
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from whitney.checks import Evidence, registry

EVENTS = Path(__file__).parent / "data" / "kilo-events"
CHECKS = ("skill_loaded", "skill_not_loaded", "subagent_delegated", "subagent_not_delegated")
NEGATIVE = ("skill_not_loaded", "subagent_not_delegated")
PARAMETER = {"skill_loaded": "skill", "skill_not_loaded": "skill", "subagent_delegated": "agent", "subagent_not_delegated": "agent"}


def lines(case: str) -> list[dict]:
    return [json.loads(line) for line in (EVENTS / f"{case}.jsonl").read_text().splitlines()]


def stream(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def captured(case: str, **changes) -> Evidence:
    """The evidence of a saved run: its events, stderr and exit code."""
    meta = json.loads((EVENTS / f"{case}.meta.json").read_text())
    run = {"events": (EVENTS / f"{case}.jsonl").read_text(), "stderr": meta["stderr"], "exit_code": meta["exit_code"]}
    return Evidence(**(run | changes))


def judge(check: str, name: str, evidence: Evidence):
    return registry()[check].run(evidence, {PARAMETER[check]: name})


def outcome(check: str, name: str, evidence: Evidence) -> str:
    verdict = judge(check, name, evidence)
    return registry()[check].label(verdict.outcome)


# --- What the checks declare -----------------------------------------------------------------


@pytest.mark.parametrize("name", CHECKS)
def test_each_check_judges_the_agent_on_kilo_only(name):
    spec = registry()[name]

    assert (spec.judges, spec.targets, spec.needs) == ("agent", ("kilo",), ())


def test_the_labels_put_each_checks_pass_first():
    labels = {name: registry()[name].labels for name in CHECKS}

    assert labels == {
        "skill_loaded": ("loaded", "not_loaded", "inconclusive"),
        "skill_not_loaded": ("not_loaded", "loaded", "inconclusive"),
        "subagent_delegated": ("delegated", "not_delegated", "inconclusive"),
        "subagent_not_delegated": ("not_delegated", "delegated", "inconclusive"),
    }


@pytest.mark.parametrize("name", CHECKS)
@pytest.mark.parametrize("params", [{}, {"skill": "", "agent": ""}, {"skill": "a", "agent": "a"}, {"name": "a"}])
def test_a_check_takes_exactly_its_one_name(name, params):
    with pytest.raises(ValidationError):
        registry()[name].run(Evidence(), params)


# --- Skills, on the saved runs ---------------------------------------------------------------


def test_a_completed_skill_call_is_a_load():
    evidence = captured("skill-named")

    assert outcome("skill_loaded", "release-notes", evidence) == "loaded"
    assert outcome("skill_not_loaded", "release-notes", evidence) == "loaded"
    assert "release-notes" in judge("skill_loaded", "release-notes", evidence).explanation


def test_a_skill_loaded_from_its_description_alone_counts():
    assert outcome("skill_loaded", "release-notes", captured("skill-description")) == "loaded"


def test_another_skills_load_is_not_this_skills():
    evidence = captured("skill-named")

    assert outcome("skill_loaded", "debugging", evidence) == "not_loaded"
    assert outcome("skill_not_loaded", "debugging", evidence) == "not_loaded"
    assert "release-notes" in judge("skill_loaded", "debugging", evidence).explanation


def test_a_skills_name_must_match_exactly():
    evidence = captured("skill-named")

    assert outcome("skill_loaded", "release", evidence) == "not_loaded"
    assert outcome("skill_loaded", "Release-Notes", evidence) == "not_loaded"


def test_a_run_with_tool_calls_and_no_skill_call_loaded_nothing():
    evidence = captured("subagent-description")

    assert outcome("skill_loaded", "release-notes", evidence) == "not_loaded"
    assert outcome("skill_not_loaded", "release-notes", evidence) == "not_loaded"


@pytest.mark.parametrize("check", ["skill_loaded", "skill_not_loaded"])
def test_the_order_of_loads_is_kept_for_the_score(check):
    verdict = judge(check, "canary-claude", captured("skill-folders-run"))

    assert verdict.details == {"loaded": ["canary-kilo", "canary-kilocode", "canary-claude", "canary-global-xdg"]}


def test_a_skill_loaded_twice_is_listed_twice():
    events = lines("skill-named")
    load = next(event for event in events if event["type"] == "tool_use" and event["part"]["tool"] == "skill")

    verdict = judge("skill_loaded", "release-notes", captured("skill-named", events=stream([*events, load])))

    assert verdict.details == {"loaded": ["release-notes", "release-notes"]}


def test_a_refused_skill_call_is_not_a_load():
    events = lines("skill-named")
    for event in events:
        if event["type"] == "tool_use" and event["part"]["tool"] == "skill":
            event["part"]["state"] = {"status": "error", "input": {"name": "release-notes"}, "error": "The user has specified a rule"}
    evidence = captured("skill-named", events=stream(events))

    assert outcome("skill_loaded", "release-notes", evidence) == "not_loaded"
    assert outcome("skill_not_loaded", "release-notes", evidence) == "not_loaded"
    assert judge("skill_loaded", "release-notes", evidence).details == {"loaded": []}


# --- Subagents, on the saved runs ------------------------------------------------------------


def test_a_completed_task_call_is_a_delegation():
    evidence = captured("subagent-description")

    assert outcome("subagent_delegated", "q7-helper", evidence) == "delegated"
    assert outcome("subagent_not_delegated", "q7-helper", evidence) == "delegated"
    assert judge("subagent_delegated", "q7-helper", evidence).details == {"delegated": ["q7-helper"]}


def test_a_delegation_to_another_agent_is_not_this_agents():
    evidence = captured("subagent-description")

    assert outcome("subagent_delegated", "general", evidence) == "not_delegated"
    assert outcome("subagent_not_delegated", "general", evidence) == "not_delegated"
    assert "q7-helper" in judge("subagent_delegated", "general", evidence).explanation


def test_a_task_call_refused_by_a_deny_rule_is_not_a_delegation():
    evidence = captured("task-deny")

    assert outcome("subagent_delegated", "q7-helper", evidence) == "not_delegated"
    assert outcome("subagent_not_delegated", "q7-helper", evidence) == "not_delegated"
    assert judge("subagent_delegated", "q7-helper", evidence).details == {"delegated": []}


def test_a_skill_call_is_not_a_delegation_and_a_task_call_is_not_a_load():
    assert outcome("subagent_delegated", "release-notes", captured("skill-named")) == "not_delegated"
    assert outcome("skill_loaded", "q7-helper", captured("subagent-description")) == "not_loaded"


# --- Runs that ended early -------------------------------------------------------------------


@pytest.mark.parametrize(
    "check, name, case",
    [
        ("skill_loaded", "release-notes", "skill-named"),
        ("skill_loaded", "debugging", "skill-named"),
        ("skill_not_loaded", "debugging", "skill-named"),
        ("subagent_delegated", "q7-helper", "subagent-description"),
        ("subagent_delegated", "general", "subagent-description"),
        ("subagent_not_delegated", "general", "subagent-description"),
    ],
)
def test_a_run_that_ended_early_shows_neither_a_pass_nor_that_nothing_happened(check, name, case):
    verdict = judge(check, name, captured(case, exit_code=1))

    assert verdict.outcome == "inconclusive"
    assert "ended early" in verdict.explanation


@pytest.mark.parametrize("check, name, case", [
    ("skill_not_loaded", "release-notes", "skill-named"),
    ("subagent_not_delegated", "q7-helper", "subagent-description"),
])
def test_a_load_or_delegation_seen_before_an_early_end_still_fails_the_negative_check(check, name, case):
    assert judge(check, name, captured(case, exit_code=1)).outcome == "fail"


def test_a_run_ended_by_an_auto_rejected_permission_is_inconclusive():
    evidence = captured("bash-ask-pattern")  # exit 1, with an `error` event whose error is a string

    assert [judge(check, "x", evidence).outcome for check in CHECKS] == ["inconclusive"] * 4


def test_a_run_that_failed_before_any_model_call_is_inconclusive():
    evidence = captured("command-option-missing")  # exit 1, with `error` events whose error is an object

    assert [judge(check, "x", evidence).outcome for check in CHECKS] == ["inconclusive"] * 4


# --- Streams the parser doesn't recognise ----------------------------------------------------


@pytest.mark.parametrize("events", ["", "not json\n", "[1, 2]\n\"text\"\n", '{"kind": "tool_use"}\n'])
@pytest.mark.parametrize("check", CHECKS)
def test_a_stream_with_no_event_the_parser_knows_is_inconclusive(check, events):
    verdict = judge(check, "release-notes", Evidence(events=events, exit_code=0))

    assert verdict.outcome == "inconclusive"
    assert "no event" in verdict.explanation


def test_a_reply_with_no_tool_call_fails_the_positive_checks_and_cannot_pass_the_negative_ones():
    """A negative check passes only on a stream holding a tool call the parser recognises (spec, "Check details")."""
    replied = stream([event for event in lines("skill-named") if event["type"] != "tool_use"])
    evidence = Evidence(events=replied, exit_code=0)

    assert outcome("skill_loaded", "release-notes", evidence) == "not_loaded"
    assert outcome("subagent_delegated", "q7-helper", evidence) == "not_delegated"
    for check in NEGATIVE:
        verdict = judge(check, "release-notes", evidence)
        assert verdict.outcome == "inconclusive"
        assert "no tool call" in verdict.explanation


def _changed(case: str, change) -> str:
    events = lines(case)
    for event in events:
        if event["type"] == "tool_use":
            change(event)
    return stream(events)


def _rename_tool(event):
    event["part"]["toolName"] = event["part"].pop("tool")


def _rename_state(event):
    event["part"]["result"] = event["part"].pop("state")


def _unknown_status(event):
    event["part"]["state"]["status"] = "done"


def _drop_input(event):
    del event["part"]["state"]["input"]


def _part_is_a_string(event):
    event["part"] = "tool"


def _part_type_is_not_tool(event):
    event["part"]["type"] = "call"


@pytest.mark.parametrize(
    "change", [_rename_tool, _rename_state, _unknown_status, _drop_input, _part_is_a_string, _part_type_is_not_tool]
)
@pytest.mark.parametrize("check, name, case", [
    ("skill_loaded", "release-notes", "skill-named"),
    ("skill_not_loaded", "debugging", "skill-named"),
    ("subagent_delegated", "q7-helper", "subagent-description"),
    ("subagent_not_delegated", "general", "subagent-description"),
])
def test_a_tool_call_in_a_shape_the_parser_doesnt_know_makes_every_check_inconclusive(check, name, case, change):
    verdict = judge(check, name, captured(case, events=_changed(case, change)))

    assert verdict.outcome == "inconclusive"
    assert "couldn't be read" in verdict.explanation


@pytest.mark.parametrize("check, name, case, key", [
    ("skill_loaded", "release-notes", "skill-named", "name"),
    ("skill_not_loaded", "debugging", "skill-named", "name"),
    ("subagent_delegated", "q7-helper", "subagent-description", "subagent_type"),
    ("subagent_not_delegated", "general", "subagent-description", "subagent_type"),
])
@pytest.mark.parametrize("value", [None, 7, ["release-notes"], ""])
def test_a_skill_or_task_call_that_doesnt_name_its_target_is_inconclusive(check, name, case, key, value):
    def unname(event):
        if event["part"]["tool"] in ("skill", "task"):
            event["part"]["state"]["input"][key] = value

    verdict = judge(check, name, captured(case, events=_changed(case, unname)))

    assert verdict.outcome == "inconclusive"
    assert "couldn't be read" in verdict.explanation


def test_one_unreadable_call_doesnt_hide_a_load_that_was_read():
    events = lines("skill-folders-run")
    first = next(event for event in events if event["type"] == "tool_use")
    first["part"]["state"]["input"] = {"skill": "canary-kilo"}
    evidence = captured("skill-folders-run", events=stream(events))

    assert judge("skill_loaded", "canary-claude", evidence).outcome == "pass"
    assert judge("skill_not_loaded", "canary-claude", evidence).outcome == "fail"
    assert judge("skill_loaded", "canary-kilo", evidence).outcome == "inconclusive"
    assert judge("skill_not_loaded", "canary-kilo", evidence).outcome == "inconclusive"


def test_a_step_that_ended_in_tool_calls_with_no_visible_tool_part_doesnt_break_the_checks():
    """skill-description.jsonl has such a step (docs/spike-findings.md, "Phase 5: observations for later tasks")."""
    evidence = captured("skill-description")

    assert outcome("skill_not_loaded", "debugging", evidence) == "not_loaded"
    assert outcome("skill_not_loaded", "release-notes", evidence) == "loaded"


# --- The score keeps what a check recorded ---------------------------------------------------


def test_what_a_check_recorded_survives_the_early_end_rule():
    verdict = judge("skill_loaded", "canary-kilo", captured("skill-folders-run", exit_code=1))

    assert verdict.outcome == "inconclusive"
    assert verdict.details == {"loaded": ["canary-kilo", "canary-kilocode", "canary-claude", "canary-global-xdg"]}
