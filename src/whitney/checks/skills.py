"""The checks skill_loaded, skill_not_loaded, subagent_delegated and subagent_not_delegated.

They read Kilo's event stream (`kilo run --format json`) for the two tool calls
plan Task 16 captured on Kilo 7.8.18 (docs/spike-findings.md, "Phase 5:
confirmed fields"; the streams are under tests/data/kilo-events/):

- a skill loads through a `skill` call whose `part.state.input.name` is the
  skill's name (fixtures skill-named, skill-description, skill-folders-run);
- a primary agent delegates through a `task` call whose
  `part.state.input.subagent_type` is the agent's name (subagent-description).

Only a call whose `part.state.status` is `completed` counts. A refused call,
`status` `error` (task-deny), loaded or delegated nothing.

A call the checks read settles a verdict: the skill loaded, or the agent was
delegated to. The absence of a call settles one only if nothing in the stream
could be hiding it (_doubt): every line is an event of a known type, every
tool call is in the captured shape, and every step that ended in tool calls
shows at least one. The negative checks also need at least one tool call in
the stream. So most changes in Kilo's output make a check inconclusive
rather than wrong. One change they can't see: if Kilo renamed the `skill` or
`task` tool and kept the shape, a load would read as another tool's call.

A subagent's own tool calls aren't in the parent's stream. So the delegation
checks say only that the `task` call was made, not what the subagent did; a
delegation made by a subagent isn't seen; and once any subagent ran, a skill
it might have loaded can't be ruled out, so the skill checks are
inconclusive unless a load was seen.
"""

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from whitney.checks import Evidence, Verdict, _listed, check

# The `type` of every event seen in the captures (docs/spike-findings.md, "The event stream").
EVENT_TYPES = ("step_start", "text", "tool_use", "step_finish", "error")
TOOL_STATUSES = ("completed", "error")


class SkillParams(BaseModel):
    """skill_loaded's and skill_not_loaded's parameter.

    Attributes:
        skill: the skill's name, as its SKILL.md's `name` gives it.
    """

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1)


class AgentParams(BaseModel):
    """subagent_delegated's and subagent_not_delegated's parameter.

    Attributes:
        agent: the agent's name: its file's name under .kilo/agents/, without `.md`.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1)


@dataclass(frozen=True)
class _Calls:
    """What the stream shows of one tool (`skill` or `task`).

    Attributes:
        names: the names its completed calls gave, in order.
        any_tool_call: True if the parser read a call of any tool.
        doubt: why a call of this tool might be in the run and not in
            `names`, or None if the stream was read in full.
    """

    names: list[str]
    any_tool_call: bool
    doubt: str | None


def skill_loaded(evidence: Evidence, params: SkillParams) -> Verdict:
    """Did the agent load the skill?

    Pass if a completed `skill` call named it and the run completed. Fail if
    the run completed and its stream, read in full, has no such call.
    Otherwise inconclusive: the run ended early (CheckSpec.run, for a load
    that was seen), a subagent ran, or something in the stream couldn't be
    read (_doubt).

    Reads: evidence.events (the `skill` tool calls, fixture skill-named) and the run's exit.
    """
    return _happened(evidence, _calls(evidence.events, "skill", "name"), params.skill, "loaded", "skill")


def skill_not_loaded(evidence: Evidence, params: SkillParams) -> Verdict:
    """Did the agent leave the skill unloaded?

    Fail if a completed `skill` call named it, even if the run then ended
    early. Pass if the run completed and its stream, read in full, holds at
    least one tool call, none of them a load of this skill or a completed
    `task` call. Otherwise inconclusive.

    Reads: evidence.events (the `skill` tool calls, fixture skill-named). The
    run's exit is read by CheckSpec.run, which keeps an early end from passing.
    """
    return _did_not_happen(_calls(evidence.events, "skill", "name"), params.skill, "loaded", "skill")


def subagent_delegated(evidence: Evidence, params: AgentParams) -> Verdict:
    """Did the agent hand work to the subagent?

    Pass if a completed `task` call named it and the run completed. Fail if
    the run completed and its stream, read in full, has no such call.
    Otherwise inconclusive, as for skill_loaded.

    Reads: evidence.events (the `task` tool calls, fixture subagent-description) and the run's exit.
    """
    return _happened(evidence, _calls(evidence.events, "task", "subagent_type"), params.agent, "delegated", "subagent")


def subagent_not_delegated(evidence: Evidence, params: AgentParams) -> Verdict:
    """Did the agent keep the work from the subagent?

    Fail if a completed `task` call named it, even if the run then ended
    early. Pass if the run completed and its stream, read in full, holds at
    least one tool call and none delegated to it. Otherwise inconclusive. A
    delegation made by another subagent isn't in the stream, so it isn't seen.

    Reads: evidence.events (the `task` tool calls, fixture subagent-description). The
    run's exit is read by CheckSpec.run, which keeps an early end from passing.
    """
    return _did_not_happen(_calls(evidence.events, "task", "subagent_type"), params.agent, "delegated", "subagent")


# For each kind of source a `trigger` block tests (evalfile.under_test): the tool that uses it and the key naming it.
USED_BY = {"skill": ("skill", "name"), "agent": ("task", "subagent_type")}


def was_used(events: str, kind: str, name: str) -> bool | None:
    """Whether the run loaded the skill, or delegated to the agent, named `name`; None if the stream can't say.

    For the report's count of the template runs in which the skill or agent
    under test was used, whatever each scenario's own checks expect.

    Args:
        events: Kilo's event stream.
        kind: "skill" or "agent".
        name: the skill's or the agent's name.
    """
    calls = _calls(events, *USED_BY[kind])
    if name in calls.names:
        return True
    return None if calls.doubt else False


def _happened(evidence: Evidence, calls: _Calls, name: str, verb: str, kind: str) -> Verdict:
    """The verdict of a check that expects `name` among the calls' names."""
    details = {verb: calls.names}
    if name in calls.names:
        return Verdict("pass", f"the {kind} {name} was {verb}", details=details)
    if calls.doubt:
        return Verdict("inconclusive", f"no call that {verb} the {kind} {name} was seen, but {calls.doubt}", details=details)
    if not evidence.run_completed():
        return Verdict("inconclusive", f"the {kind} {name} wasn't {verb}, but the run ended early", details=details)
    return Verdict("fail", f"the {kind} {name} wasn't {verb}; {_seen(calls, verb)}", details=details)


def _did_not_happen(calls: _Calls, name: str, verb: str, kind: str) -> Verdict:
    """The verdict of a check that expects `name` not to be among the calls' names."""
    details = {verb: calls.names}
    if name in calls.names:
        return Verdict("fail", f"the {kind} {name} was {verb}", details=details)
    if calls.doubt:
        return Verdict("inconclusive", f"no call that {verb} the {kind} {name} was seen, but {calls.doubt}", details=details)
    if not calls.any_tool_call:
        return Verdict(
            "inconclusive",
            f"the stream holds no tool call, so it can't show the {kind} {name} wasn't {verb}",
            details=details,
        )
    return Verdict("pass", f"the {kind} {name} wasn't {verb}; {_seen(calls, verb)}", details=details)


def _seen(calls: _Calls, verb: str) -> str:
    return f"{verb}: {_listed(calls.names)}" if calls.names else f"nothing was {verb}"


def _calls(events: str, tool: str, name_key: str) -> _Calls:
    """Read the stream for `tool`'s completed calls, each named by `part.state.input[name_key]`."""
    names: list[str] = []
    known = unparsed = tool_calls = unreadable = hidden = subagents = 0
    in_step = 0  # the tool_use events since the last step_start, readable or not
    for line in events.splitlines():
        event = _event(line)
        if event is None:
            unparsed += bool(line.strip())
            continue
        known += 1
        if event["type"] == "step_start":
            in_step = 0
        elif event["type"] == "step_finish":
            hidden += _ended_in_tool_calls(event) and not in_step
        if event["type"] != "tool_use":
            continue
        in_step += 1
        call = _tool_call(event)
        if call is None:
            unreadable += 1
            continue
        called, status, given = call
        tool_calls += 1
        subagents += called == "task" and status == "completed"
        if called != tool:
            continue
        name = given.get(name_key)
        if not isinstance(name, str) or not name:
            # One of this tool's calls, and which skill or agent it names is unknown.
            unreadable += 1
        elif status == "completed":
            names.append(name)
    doubt = _doubt(known, unparsed, unreadable, hidden, subagents if tool == "skill" else 0)
    return _Calls(names, bool(tool_calls), doubt)


def _doubt(known: int, unparsed: int, unreadable: int, hidden: int, subagents: int) -> str | None:
    """Why a call might be in the run and not among those read, or None.

    Args:
        known: events of a known type.
        unparsed: other lines that aren't blank: not JSON, or an event of a type the captures don't have.
        unreadable: tool calls not in the captured shape.
        hidden: steps that ended in tool calls and show none. One fixture has
            such a step (docs/spike-findings.md, "Phase 5: observations for later tasks").
        subagents: completed `task` calls, when the calls looked for are one a subagent could make unseen.
    """
    if not known:
        return "the stream holds no event the parser knows"
    if unparsed:
        return f"{unparsed} line(s) of the stream couldn't be read as events"
    if unreadable:
        return f"{unreadable} tool call(s) couldn't be read"
    if hidden:
        return f"{hidden} step(s) ended in tool calls the stream doesn't show"
    if subagents:
        return f"{subagents} subagent(s) ran, and a subagent's own tool calls aren't in the stream"
    return None


def _event(line: str) -> dict | None:
    """The line as an event of a known type, or None."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) and event.get("type") in EVENT_TYPES else None


def _ended_in_tool_calls(event: dict) -> bool:
    """True if a `step_finish` event says its step ended in tool calls."""
    part = event.get("part")
    return isinstance(part, dict) and part.get("reason") == "tool-calls"


def _tool_call(event: dict) -> tuple[str, str, dict[str, Any]] | None:
    """A `tool_use` event's tool, status and input, or None if it isn't in the captured shape."""
    part = event.get("part")
    if not isinstance(part, dict) or part.get("type") != "tool":
        return None
    tool, state = part.get("tool"), part.get("state")
    if not isinstance(tool, str) or not isinstance(state, dict):
        return None
    status, given = state.get("status"), state.get("input")
    if status not in TOOL_STATUSES or not isinstance(given, dict):
        return None
    return tool, status, given


check(
    "skill_loaded",
    params=SkillParams,
    judges="agent",
    labels=("loaded", "not_loaded", "inconclusive"),
    targets=("kilo",),
)(skill_loaded)

check(
    "skill_not_loaded",
    params=SkillParams,
    judges="agent",
    labels=("not_loaded", "loaded", "inconclusive"),
    targets=("kilo",),
)(skill_not_loaded)

check(
    "subagent_delegated",
    params=AgentParams,
    judges="agent",
    labels=("delegated", "not_delegated", "inconclusive"),
    targets=("kilo",),
)(subagent_delegated)

check(
    "subagent_not_delegated",
    params=AgentParams,
    judges="agent",
    labels=("not_delegated", "delegated", "inconclusive"),
    targets=("kilo",),
)(subagent_not_delegated)
