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

Nothing else in the stream is trusted to say a skill didn't load. A tool call
the parser can't read might be the load, so it makes a check inconclusive
unless a call it could read already settles the verdict. The negative checks
pass only on a stream holding at least one tool call the parser read, so a
change in Kilo's output makes them inconclusive rather than passing.

A subagent's own tool calls aren't in the parent's stream, so the delegation
checks say only that the `task` call was made, not what the subagent did.
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
        tool_calls: how many tool calls of any tool the parser read.
        unreadable: how many tool calls it couldn't read, any of which might be one of this tool's.
        events: how many events of a known type the stream held.
    """

    names: list[str]
    tool_calls: int
    unreadable: int
    events: int


def skill_loaded(evidence: Evidence, params: SkillParams) -> Verdict:
    """Did the agent load the skill?

    Pass if a completed `skill` call named it. Fail if the run completed and
    its stream, read in full, has no such call. Otherwise inconclusive: the
    run ended early, a tool call couldn't be read, or the stream held no
    event the parser knows.

    Reads: evidence.events (the `skill` tool calls, fixture skill-named) and the run's exit.
    """
    return _happened(evidence, _calls(evidence.events, "skill", "name"), params.skill, "loaded", "skill")


def skill_not_loaded(evidence: Evidence, params: SkillParams) -> Verdict:
    """Did the agent leave the skill unloaded?

    Fail if a completed `skill` call named it, even if the run then ended
    early. Pass if the stream holds at least one tool call, every tool call
    could be read, and none loaded it. Otherwise inconclusive.

    Reads: evidence.events (the `skill` tool calls, fixture skill-named) and the run's exit.
    """
    return _did_not_happen(_calls(evidence.events, "skill", "name"), params.skill, "loaded", "skill")


def subagent_delegated(evidence: Evidence, params: AgentParams) -> Verdict:
    """Did the agent hand work to the subagent?

    Pass if a completed `task` call named it. Fail if the run completed and
    its stream, read in full, has no such call. Otherwise inconclusive, as
    for skill_loaded.

    Reads: evidence.events (the `task` tool calls, fixture subagent-description) and the run's exit.
    """
    return _happened(evidence, _calls(evidence.events, "task", "subagent_type"), params.agent, "delegated", "subagent")


def subagent_not_delegated(evidence: Evidence, params: AgentParams) -> Verdict:
    """Did the agent keep the work from the subagent?

    Fail if a completed `task` call named it, even if the run then ended
    early. Pass if the stream holds at least one tool call, every tool call
    could be read, and none delegated to it. Otherwise inconclusive.

    Reads: evidence.events (the `task` tool calls, fixture subagent-description) and the run's exit.
    """
    return _did_not_happen(_calls(evidence.events, "task", "subagent_type"), params.agent, "delegated", "subagent")


def _happened(evidence: Evidence, calls: _Calls, name: str, verb: str, kind: str) -> Verdict:
    """The verdict of a check that expects `name` among the calls' names."""
    details = {verb: calls.names}
    if name in calls.names:
        return Verdict("pass", f"the {kind} {name} was {verb}", details=details)
    doubt = _doubt(calls)
    if doubt:
        return Verdict("inconclusive", f"no call {verb} the {kind} {name} was seen, but {doubt}", details=details)
    if not evidence.run_completed():
        return Verdict("inconclusive", f"the {kind} {name} wasn't {verb}, but the run ended early", details=details)
    return Verdict("fail", f"the {kind} {name} wasn't {verb}; {_seen(calls, verb)}", details=details)


def _did_not_happen(calls: _Calls, name: str, verb: str, kind: str) -> Verdict:
    """The verdict of a check that expects `name` not to be among the calls' names."""
    details = {verb: calls.names}
    if name in calls.names:
        return Verdict("fail", f"the {kind} {name} was {verb}", details=details)
    doubt = _doubt(calls)
    if doubt:
        return Verdict("inconclusive", f"no call {verb} the {kind} {name} was seen, but {doubt}", details=details)
    if not calls.tool_calls:
        return Verdict(
            "inconclusive",
            f"the stream holds no tool call, so it can't show the {kind} {name} wasn't {verb}",
            details=details,
        )
    return Verdict("pass", f"the {kind} {name} wasn't {verb}; {_seen(calls, verb)}", details=details)


def _doubt(calls: _Calls) -> str | None:
    """Why the stream can't show a call didn't happen, or None if it was read in full."""
    if not calls.events:
        return "the stream holds no event the parser knows"
    if calls.unreadable:
        return f"{calls.unreadable} tool call(s) couldn't be read"
    return None


def _seen(calls: _Calls, verb: str) -> str:
    return f"{verb}: {_listed(calls.names)}" if calls.names else f"nothing was {verb}"


def _calls(events: str, tool: str, name_key: str) -> _Calls:
    """Read the stream for `tool`'s completed calls, each named by `part.state.input[name_key]`."""
    names: list[str] = []
    tool_calls = unreadable = known = 0
    for event in _events(events):
        known += 1
        if event["type"] != "tool_use":
            continue
        call = _tool_call(event)
        if call is None:
            unreadable += 1
            continue
        called, status, given = call
        tool_calls += 1
        if called != tool:
            continue
        name = given.get(name_key)
        if not isinstance(name, str) or not name:
            # Counted as unreadable too: it is one of this tool's calls, and which skill or agent it names is unknown.
            unreadable += 1
        elif status == "completed":
            names.append(name)
    return _Calls(names, tool_calls, unreadable, known)


def _events(events: str) -> list[dict]:
    """The stream's events of a known type; any other line is left out."""
    known = []
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") in EVENT_TYPES:
            known.append(event)
    return known


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
