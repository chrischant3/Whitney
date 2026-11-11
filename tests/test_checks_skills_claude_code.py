"""The skill and subagent checks on Claude Code's stream.

They are judged on the streams Claude Code 2.1.322 really printed, saved by
spikes/claude_code_shapes.py under tests/data/claude-code-events/
(docs/spike-findings.md, "Phase 5 Task 36"), and on those streams changed by
hand to show what a different output would do.
"""

import json
from pathlib import Path

import pytest

from whitney.checks import Evidence, registry
from whitney.checks.skills import was_used

EVENTS = Path(__file__).parent / "data" / "claude-code-events"
KILO_EVENTS = Path(__file__).parent / "data" / "kilo-events"
CHECKS = ("skill_loaded", "skill_not_loaded", "subagent_delegated", "subagent_not_delegated")
PARAMETER = {"skill_loaded": "skill", "skill_not_loaded": "skill", "subagent_delegated": "agent", "subagent_not_delegated": "agent"}
SKILL, HELPER, WRITER = "release-notes", "q7-helper", "n4-writer"


def lines(case: str) -> list[dict]:
    return [json.loads(line) for line in (EVENTS / f"{case}.jsonl").read_text().splitlines()]


def stream(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def captured(case: str, **changes) -> Evidence:
    """The evidence of a saved run: its events, stderr, exit code and whether it ended early."""
    meta = json.loads((EVENTS / f"{case}.meta.json").read_text())
    run = {
        "events": (EVENTS / f"{case}.jsonl").read_text(), "stderr": meta["stderr"],
        "exit_code": meta["exit_code"], "ended_early": meta["ended_early"],
    }
    return Evidence(**(run | changes))


def changed(case: str, events: list[dict]) -> Evidence:
    return captured(case, events=stream(events))


def judge(check: str, name: str, evidence: Evidence):
    return registry()[check].run(evidence, {PARAMETER[check]: name})


def outcome(check: str, name: str, evidence: Evidence) -> str:
    return registry()[check].label(judge(check, name, evidence).outcome)


def blocks(event: dict, kind: str) -> list[dict]:
    """The event's content blocks of one type, or [] for an event without a message."""
    return [block for block in event.get("message", {}).get("content", []) if block["type"] == kind]


def tool_uses(events: list[dict], name: str) -> list[dict]:
    return [block for event in events for block in blocks(event, "tool_use") if block["name"] == name]


def without_result_of(events: list[dict], call: dict) -> list[dict]:
    """The stream without the event that holds `call`'s result."""
    return [e for e in events if not any(b["tool_use_id"] == call["id"] for b in blocks(e, "tool_result"))]


# --- What the checks declare -----------------------------------------------------------------


@pytest.mark.parametrize("name", CHECKS)
def test_each_check_supports_claude_code(name):
    assert registry()[name].targets == ("kilo", "claude-code")


@pytest.mark.parametrize("name", CHECKS)
def test_each_checks_docstring_names_the_claude_code_version_and_spike_it_relies_on(name):
    docstring = registry()[name].function.__doc__

    assert "Claude Code 2.1.322" in docstring
    assert "docs/spike-findings.md" in docstring


# --- Skills, on the saved runs ---------------------------------------------------------------


def test_a_skill_call_that_worked_is_a_load():
    evidence = captured("skill-named")

    assert outcome("skill_loaded", SKILL, evidence) == "loaded"
    assert outcome("skill_not_loaded", SKILL, evidence) == "loaded"
    assert judge("skill_loaded", SKILL, evidence).explanation == "the skill release-notes was loaded"
    assert judge("skill_loaded", SKILL, evidence).details == {"loaded": [SKILL]}


@pytest.mark.parametrize("case", ["skill-description", "skill-not-loaded"])
def test_a_run_with_tool_calls_and_no_skill_call_left_the_skill_unloaded(case):
    evidence = captured(case)

    assert outcome("skill_loaded", SKILL, evidence) == "not_loaded"
    assert outcome("skill_not_loaded", SKILL, evidence) == "not_loaded"
    assert judge("skill_not_loaded", SKILL, evidence).explanation == "the skill release-notes wasn't loaded; nothing was loaded"


def test_another_skills_load_is_not_this_skills():
    evidence = captured("skill-named")

    assert outcome("skill_loaded", "debugging", evidence) == "not_loaded"
    assert outcome("skill_not_loaded", "debugging", evidence) == "not_loaded"
    assert judge("skill_loaded", "debugging", evidence).explanation == "the skill debugging wasn't loaded; loaded: release-notes"


def test_a_run_with_no_tool_call_cant_show_the_skill_was_left_alone():
    evidence = captured("no-tool-call")

    assert outcome("skill_loaded", SKILL, evidence) == "not_loaded"
    verdict = judge("skill_not_loaded", SKILL, evidence)
    assert verdict.outcome == "inconclusive"
    assert "no tool call" in verdict.explanation


def test_a_skill_call_that_failed_loaded_nothing():
    evidence = captured("skill-missing")

    assert outcome("skill_loaded", "no-such-skill-5521", evidence) == "not_loaded"
    assert outcome("skill_not_loaded", "no-such-skill-5521", evidence) == "not_loaded"
    assert judge("skill_loaded", "no-such-skill-5521", evidence).details == {"loaded": []}


# --- Subagents, on the saved runs ------------------------------------------------------------


def test_an_agent_call_that_worked_is_a_delegation():
    evidence = captured("subagent-description")

    assert outcome("subagent_delegated", HELPER, evidence) == "delegated"
    assert outcome("subagent_not_delegated", HELPER, evidence) == "delegated"
    assert judge("subagent_delegated", HELPER, evidence).explanation == "the subagent q7-helper was delegated"
    assert judge("subagent_delegated", HELPER, evidence).details == {"delegated": [HELPER]}


def test_a_run_with_tool_calls_and_no_agent_call_kept_the_work():
    evidence = captured("subagent-not-delegated")

    assert outcome("subagent_delegated", HELPER, evidence) == "not_delegated"
    assert outcome("subagent_not_delegated", HELPER, evidence) == "not_delegated"


def test_an_agent_call_that_failed_delegated_nothing():
    evidence = captured("agent-missing")

    assert outcome("subagent_delegated", "no-such-agent-5521", evidence) == "not_delegated"
    assert outcome("subagent_not_delegated", "no-such-agent-5521", evidence) == "not_delegated"


def test_another_agents_delegation_is_not_this_agents():
    evidence = captured("subagent-description")

    assert outcome("subagent_delegated", "reviewer", evidence) == "not_delegated"
    assert outcome("subagent_not_delegated", "reviewer", evidence) == "not_delegated"


# --- A subagent's own calls are in the stream ------------------------------------------------


def test_a_run_that_delegated_can_still_show_the_skill_was_left_alone():
    """On Kilo a subagent's calls are hidden, so this is inconclusive there. Claude Code prints them."""
    evidence = captured("subagent-description")

    verdict = judge("skill_not_loaded", SKILL, evidence)

    assert verdict.outcome == "pass"
    assert outcome("skill_loaded", SKILL, evidence) == "not_loaded"


def test_a_skill_the_subagent_loaded_counts_and_the_explanation_says_who_loaded_it():
    evidence = captured("subagent-loads-skill")

    assert outcome("skill_loaded", SKILL, evidence) == "loaded"
    assert outcome("skill_not_loaded", SKILL, evidence) == "loaded"
    assert judge("skill_loaded", SKILL, evidence).explanation == "the skill release-notes was loaded, by the subagent n4-writer"
    assert judge("skill_not_loaded", SKILL, evidence).explanation == "the skill release-notes was loaded, by the subagent n4-writer"
    assert outcome("subagent_delegated", WRITER, evidence) == "delegated"


def test_a_skill_the_main_agent_loaded_too_is_not_put_down_to_the_subagent():
    events = lines("subagent-loads-skill")
    by_subagent = next(e for e in events if blocks(e, "tool_use") and blocks(e, "tool_use")[0]["name"] == "Skill")
    result = next(e for e in events if any(b["tool_use_id"] == blocks(by_subagent, "tool_use")[0]["id"] for b in blocks(e, "tool_result")))
    own_call, own_result = json.loads(json.dumps(by_subagent)), json.loads(json.dumps(result))
    own_call["parent_tool_use_id"] = own_result["parent_tool_use_id"] = None
    own_call["message"]["content"][0]["id"] = own_result["message"]["content"][0]["tool_use_id"] = "toolu_own"

    verdict = judge("skill_loaded", SKILL, changed("subagent-loads-skill", [*events[:1], own_call, own_result, *events[1:]]))

    assert verdict.explanation == "the skill release-notes was loaded"
    assert verdict.details == {"loaded": [SKILL, SKILL]}


def test_a_subagents_skill_call_whose_agent_call_is_unknown_still_counts_as_a_load():
    events = [e for e in lines("subagent-loads-skill") if not tool_uses([e], "Agent")]

    verdict = judge("skill_not_loaded", SKILL, changed("subagent-loads-skill", events))

    assert verdict.outcome == "fail"
    assert verdict.explanation == "the skill release-notes was loaded, by a subagent"


# --- An early end ----------------------------------------------------------------------------


def test_a_load_in_a_run_that_ended_early_isnt_a_pass_but_is_still_a_load():
    evidence = captured("skill-named", ended_early=True)

    assert judge("skill_loaded", SKILL, evidence).outcome == "inconclusive"
    assert judge("skill_not_loaded", SKILL, evidence).outcome == "fail"


@pytest.mark.parametrize("check, name, case", [
    ("skill_loaded", SKILL, "skill-not-loaded"),
    ("skill_not_loaded", SKILL, "skill-not-loaded"),
    ("subagent_delegated", HELPER, "subagent-not-delegated"),
    ("subagent_not_delegated", HELPER, "subagent-not-delegated"),
])
@pytest.mark.parametrize("ending", [{"ended_early": True}, {"exit_code": 1}])
def test_no_call_in_a_run_that_ended_early_is_inconclusive(check, name, case, ending):
    assert judge(check, name, captured(case, **ending)).outcome == "inconclusive"


# --- A stream that can't be read in full -----------------------------------------------------


def unreadable_streams() -> dict[str, tuple[str, str]]:
    """Name -> (a stream with no Skill or Agent call that worked, what the explanation says is wrong with it)."""
    events = lines("skill-not-loaded")
    read = next(e for e in events if tool_uses([e], "Read"))
    result = next(e for e in events if blocks(e, "tool_result"))

    def with_block(event: dict, **changes) -> dict:
        copy = json.loads(json.dumps(event))
        copy["message"]["content"][0].update(changes)
        return copy

    def swapped(old: dict, new: dict) -> str:
        return stream([new if e is old else e for e in events])

    return {
        "a line that isn't JSON": (stream(events) + "\nnot json", "1 line(s) of the stream couldn't be read as events"),
        "an event of an unknown type": (stream([*events, {"type": "rate_limit_event"}]), "1 line(s) of the stream couldn't be read as events"),
        "a Kilo event": (stream([*events, {"type": "step_start"}]), "1 line(s) of the stream couldn't be read as events"),
        "a line that is JSON but no object": (stream(events) + "\n[1]", "1 line(s) of the stream couldn't be read as events"),
        "an assistant event with no message": (
            stream([*events, {"type": "assistant", "parent_tool_use_id": None}]), "1 tool call(s) or message(s) couldn't be read",
        ),
        "content that isn't a list": (
            stream([*events, {"type": "user", "parent_tool_use_id": None, "message": {"content": "text"}}]),
            "1 tool call(s) or message(s) couldn't be read",
        ),
        "a block that isn't an object": (
            stream([*events, {"type": "user", "parent_tool_use_id": None, "message": {"content": ["text"]}}]),
            "1 tool call(s) or message(s) couldn't be read",
        ),
        "a block of an unknown type": (swapped(read, with_block(read, type="server_tool_use")), "couldn't be read"),
        "a tool_result in an assistant event": (
            stream([*events, {**result, "type": "assistant"}]), "1 tool call(s) or message(s) couldn't be read",
        ),
        "a tool call with no name": (swapped(read, with_block(read, name=None)), "couldn't be read"),
        "a tool call with no id": (swapped(read, with_block(read, id=None)), "couldn't be read"),
        "a tool call whose input isn't an object": (swapped(read, with_block(read, input="app.py")), "couldn't be read"),
        "a result with no tool_use_id": (swapped(result, with_block(result, tool_use_id=None)), "couldn't be read"),
        "a result whose is_error isn't true or false": (swapped(result, with_block(result, is_error="no")), "couldn't be read"),
        "a parent that isn't a tool call's id": (swapped(read, {**read, "parent_tool_use_id": 7}), "couldn't be read"),
        "a tool call with no result": (stream(without_result_of(events, tool_uses(events, "Read")[0])), "1 tool call(s) have no result in the stream"),
        "a result of a call the stream doesn't show": (
            stream([e for e in events if e is not read]), "1 result(s) are of tool calls the stream doesn't show",
        ),
        "a Skill call that doesn't name a skill": (
            swapped(read, with_block(read, name="Skill", input={"skill": ""})), "1 tool call(s) or message(s) couldn't be read",
        ),
    }


@pytest.mark.parametrize("problem", unreadable_streams())
@pytest.mark.parametrize("check", ["skill_loaded", "skill_not_loaded"])
def test_no_load_seen_in_a_stream_that_cant_be_read_in_full_is_inconclusive(check, problem):
    events, says = unreadable_streams()[problem]

    verdict = judge(check, SKILL, captured("skill-not-loaded", events=events))

    assert verdict.outcome == "inconclusive"
    assert says in verdict.explanation
    assert verdict.explanation.startswith("no call that loaded the skill release-notes was seen, but ")


def test_a_call_of_the_other_tool_that_doesnt_name_its_target_leaves_this_one_readable():
    """An Agent call with no subagent_type hides no skill load."""
    events = lines("skill-not-loaded")
    read = next(e for e in events if tool_uses([e], "Read"))
    odd = json.loads(json.dumps(read))
    odd["message"]["content"][0].update(name="Agent", input={})

    evidence = changed("skill-not-loaded", [odd if e is read else e for e in events])

    assert judge("skill_not_loaded", SKILL, evidence).outcome == "pass"
    assert judge("subagent_not_delegated", HELPER, evidence).outcome == "inconclusive"


def test_a_load_that_was_seen_stands_in_a_stream_that_cant_be_read_in_full():
    evidence = captured("skill-named", events=(EVENTS / "skill-named.jsonl").read_text() + "\nnot json")

    assert outcome("skill_loaded", SKILL, evidence) == "loaded"
    assert judge("skill_not_loaded", SKILL, evidence).outcome == "fail"


def test_a_skill_call_with_no_result_is_not_a_load_and_not_its_absence():
    events = lines("skill-named")

    evidence = changed("skill-named", without_result_of(events, tool_uses(events, "Skill")[0]))

    assert judge("skill_loaded", SKILL, evidence).outcome == "inconclusive"
    assert judge("skill_not_loaded", SKILL, evidence).outcome == "inconclusive"
    assert judge("skill_loaded", SKILL, evidence).details == {"loaded": []}


@pytest.mark.parametrize("events", ["", "\n\n", "not json"])
@pytest.mark.parametrize("check", CHECKS)
def test_a_stream_with_no_event_is_inconclusive(check, events):
    verdict = judge(check, "anything", Evidence(exit_code=0, events=events))

    assert verdict.outcome == "inconclusive"


def test_a_stream_that_starts_with_a_claude_code_event_is_read_as_claude_codes():
    """So a Kilo event after it is a line that can't be read, not a reason to read the stream as Kilo's."""
    kilo = (KILO_EVENTS / "skill-named.jsonl").read_text()
    events = (EVENTS / "skill-not-loaded.jsonl").read_text() + "\n" + kilo

    verdict = judge("skill_loaded", SKILL, captured("skill-not-loaded", events=events))

    assert verdict.outcome == "inconclusive"
    assert "couldn't be read as events" in verdict.explanation


def test_a_stream_that_starts_with_a_kilo_event_is_read_as_kilos():
    kilo = (KILO_EVENTS / "skill-named.jsonl").read_text()
    events = kilo + "\n" + (EVENTS / "skill-named.jsonl").read_text()

    assert judge("skill_loaded", SKILL, Evidence(exit_code=0, events=kilo)).outcome == "pass"
    assert judge("skill_not_loaded", "debugging", Evidence(exit_code=0, events=events)).outcome == "inconclusive"


def test_blank_lines_and_unreadable_ones_before_the_first_event_dont_decide_whose_stream_it_is():
    events = "\nnot json\n[1]\n" + (EVENTS / "skill-named.jsonl").read_text()

    assert judge("skill_not_loaded", SKILL, captured("skill-named", events=events)).outcome == "fail"


# --- Whether the skill or agent under test was used, for the report --------------------------


@pytest.mark.parametrize("case, kind, name, used", [
    ("skill-named", "skill", SKILL, True),
    ("skill-not-loaded", "skill", SKILL, False),
    ("skill-named", "skill", "debugging", False),
    ("subagent-loads-skill", "skill", SKILL, True),
    ("subagent-description", "skill", SKILL, False),
    ("subagent-description", "agent", HELPER, True),
    ("subagent-not-delegated", "agent", HELPER, False),
    ("no-tool-call", "agent", HELPER, False),
])
def test_whether_the_skill_or_agent_under_test_was_used_is_read_from_claude_codes_stream(case, kind, name, used):
    assert was_used((EVENTS / f"{case}.jsonl").read_text(), kind, name) is used


def test_a_claude_code_stream_that_cant_be_read_in_full_cant_say_it_wasnt_used():
    events = (EVENTS / "skill-not-loaded.jsonl").read_text() + "\nnot json"

    assert was_used(events, "skill", SKILL) is None
