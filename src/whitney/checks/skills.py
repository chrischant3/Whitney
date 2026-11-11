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

On the claude-code target they read Claude Code's stream instead
(`claude -p --output-format stream-json --verbose`), for the two tool calls
plan Task 36 captured on Claude Code 2.1.322 (docs/spike-findings.md, "Phase 5
Task 36"; the streams are under tests/data/claude-code-events/):

- a skill loads through a `Skill` call whose `input.skill` is the skill's
  name (fixture skill-named);
- an agent delegates through an `Agent` call whose `input.subagent_type` is
  the agent's name (subagent-description).

A call is a `tool_use` block in an `assistant` event, and its result a
`tool_result` block in a `user` event with the call's id. Only a call whose
result isn't an error counts (skill-missing, agent-missing). A call with no
result in the stream is neither a load nor its absence. A subagent's own
calls are in this stream, in events whose `parent_tool_use_id` is the `Agent`
call's id (subagent-loads-skill). So a skill a subagent loaded counts as
loaded, the explanation says the subagent loaded it, and a run that
delegated can still show a skill was left alone. One hand-off isn't counted:
a skill can run as a forked subagent, with no `Agent` call (skill-forked,
where the built-in code-review skill did), and the delegation checks read
only `Agent` calls. As on Kilo, the absence of a
call settles a verdict only if every line is an event of a captured type and
every message, call and result is in the captured shape.

Which stream it is, is read from its first event: Kilo's event types and
Claude Code's have no name in common, and an event of the other harness's
type is a line that couldn't be read.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from whitney.checks import Evidence, Verdict, _listed, check

# The `type` of every event seen in the captures (docs/spike-findings.md, "The event stream").
EVENT_TYPES = ("step_start", "text", "tool_use", "step_finish", "error")
TOOL_STATUSES = ("completed", "error")
# For each kind of source a `trigger` block tests (evalfile.under_test): Kilo's tool that uses it and the key naming it.
USED_BY = {"skill": ("skill", "name"), "agent": ("task", "subagent_type")}
# The same for Claude Code, and the `type` of every event and content block seen in its captures.
CLAUDE_USED_BY = {"skill": ("Skill", "skill"), "agent": ("Agent", "subagent_type")}
# `tool_progress` is a heartbeat Claude Code prints every 30 seconds of a long tool call (fixture skill-forked).
CLAUDE_EVENT_TYPES = ("system", "assistant", "user", "result", "tool_progress")
CLAUDE_BLOCK_TYPES = {"assistant": ("thinking", "text", "tool_use"), "user": ("text", "tool_result")}


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
        by: name -> who made its calls, for a name only subagents gave
            (Claude Code): "the subagent <its name>", or "a subagent" if the
            stream doesn't show which.
    """

    names: list[str]
    any_tool_call: bool
    doubt: str | None
    by: Mapping[str, str] = field(default_factory=dict)


def skill_loaded(evidence: Evidence, params: SkillParams) -> Verdict:
    """Did the agent load the skill?

    Pass if a completed `skill` call named it and the run completed. Fail if
    the run completed and its stream, read in full, has no such call.
    Otherwise inconclusive: the run ended early (CheckSpec.run, for a load
    that was seen), a subagent ran, or something in the stream couldn't be
    read (_doubt).

    Reads: evidence.events (the `skill` tool calls, fixture skill-named) and the run's exit.
    On claude-code, the `Skill` calls of Claude Code 2.1.322, a subagent's
    among them (docs/spike-findings.md, "Phase 5 Task 36").
    """
    return _happened(evidence, _calls(evidence.events, "skill"), params.skill, "loaded", "skill")


def skill_not_loaded(evidence: Evidence, params: SkillParams) -> Verdict:
    """Did the agent leave the skill unloaded?

    Fail if a completed `skill` call named it, even if the run then ended
    early. Pass if the run completed and its stream, read in full, holds at
    least one tool call, none of them a load of this skill or a completed
    `task` call. Otherwise inconclusive.

    Reads: evidence.events (the `skill` tool calls, fixture skill-named). The
    run's exit is read by CheckSpec.run, which keeps an early end from passing.
    On claude-code, the `Skill` calls of Claude Code 2.1.322
    (docs/spike-findings.md, "Phase 5 Task 36"). A subagent's calls are in
    that stream, so a run that delegated can pass, and a load by the
    subagent fails.
    """
    return _did_not_happen(_calls(evidence.events, "skill"), params.skill, "loaded", "skill")


def subagent_delegated(evidence: Evidence, params: AgentParams) -> Verdict:
    """Did the agent hand work to the subagent?

    Pass if a completed `task` call named it and the run completed. Fail if
    the run completed and its stream, read in full, has no such call.
    Otherwise inconclusive, as for skill_loaded.

    Reads: evidence.events (the `task` tool calls, fixture subagent-description) and the run's exit.
    On claude-code, the `Agent` calls of Claude Code 2.1.322
    (docs/spike-findings.md, "Phase 5 Task 36").
    """
    return _happened(evidence, _calls(evidence.events, "agent"), params.agent, "delegated", "subagent")


def subagent_not_delegated(evidence: Evidence, params: AgentParams) -> Verdict:
    """Did the agent keep the work from the subagent?

    Fail if a completed `task` call named it, even if the run then ended
    early. Pass if the run completed and its stream, read in full, holds at
    least one tool call and none delegated to it. Otherwise inconclusive. A
    delegation made by another subagent isn't in the stream, so it isn't seen.

    Reads: evidence.events (the `task` tool calls, fixture subagent-description). The
    run's exit is read by CheckSpec.run, which keeps an early end from passing.
    On claude-code, the `Agent` calls of Claude Code 2.1.322
    (docs/spike-findings.md, "Phase 5 Task 36"), where a delegation made by
    a subagent is in the stream and counts.
    """
    return _did_not_happen(_calls(evidence.events, "agent"), params.agent, "delegated", "subagent")


def was_used(events: str, kind: str, name: str) -> bool | None:
    """Whether the run loaded the skill, or delegated to the agent, named `name`; None if the stream can't say.

    For the report's count of the template runs in which the skill or agent
    under test was used, whatever each scenario's own checks expect.

    Args:
        events: Kilo's event stream, or Claude Code's.
        kind: "skill" or "agent".
        name: the skill's or the agent's name.
    """
    calls = _calls(events, kind)
    if name in calls.names:
        return True
    return None if calls.doubt else False


def _happened(evidence: Evidence, calls: _Calls, name: str, verb: str, kind: str) -> Verdict:
    """The verdict of a check that expects `name` among the calls' names."""
    details = {verb: calls.names}
    if name in calls.names:
        return Verdict("pass", f"the {kind} {name} was {verb}{_by(calls, name)}", details=details)
    if calls.doubt:
        return Verdict("inconclusive", f"no call that {verb} the {kind} {name} was seen, but {calls.doubt}", details=details)
    if not evidence.run_completed():
        return Verdict("inconclusive", f"the {kind} {name} wasn't {verb}, but the run ended early", details=details)
    return Verdict("fail", f"the {kind} {name} wasn't {verb}; {_seen(calls, verb)}", details=details)


def _did_not_happen(calls: _Calls, name: str, verb: str, kind: str) -> Verdict:
    """The verdict of a check that expects `name` not to be among the calls' names."""
    details = {verb: calls.names}
    if name in calls.names:
        return Verdict("fail", f"the {kind} {name} was {verb}{_by(calls, name)}", details=details)
    if calls.doubt:
        return Verdict("inconclusive", f"no call that {verb} the {kind} {name} was seen, but {calls.doubt}", details=details)
    if not calls.any_tool_call:
        return Verdict(
            "inconclusive",
            f"the stream holds no tool call, so it can't show the {kind} {name} wasn't {verb}",
            details=details,
        )
    return Verdict("pass", f"the {kind} {name} wasn't {verb}; {_seen(calls, verb)}", details=details)


def _by(calls: _Calls, name: str) -> str:
    """", by the subagent <its name>" if only subagents made the calls that gave `name`, else ""."""
    return f", by {calls.by[name]}" if name in calls.by else ""


def _seen(calls: _Calls, verb: str) -> str:
    return f"{verb}: {_listed(calls.names)}" if calls.names else f"nothing was {verb}"


def _calls(events: str, kind: str) -> _Calls:
    """Read the stream, Kilo's or Claude Code's, for the calls that load a skill or delegate to an agent (`kind`)."""
    if _is_claude_codes(events):
        return _claude_calls(events, *CLAUDE_USED_BY[kind])
    return _kilo_calls(events, *USED_BY[kind])


def _is_claude_codes(events: str) -> bool:
    """True if the stream's first event of either harness's types is one of Claude Code's."""
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type") if isinstance(event, dict) else None
        if kind in CLAUDE_EVENT_TYPES or kind in EVENT_TYPES:
            return kind in CLAUDE_EVENT_TYPES
    return False


def _claude_calls(events: str, tool: str, name_key: str) -> _Calls:
    """Read Claude Code's stream for `tool`'s calls that worked, each named by `input[name_key]`.

    A call worked if its result is in the stream and isn't an error.
    """
    uses: list[tuple[str, str, dict[str, Any], str | None]] = []  # each tool call: id, tool, input, parent call's id
    failed: dict[str, bool] = {}  # a result's tool call id -> True if it is an error
    unparsed = unreadable = 0
    for line in events.splitlines():
        event = _event(line, CLAUDE_EVENT_TYPES)
        if event is None:
            unparsed += bool(line.strip())
            continue
        if event["type"] not in CLAUDE_BLOCK_TYPES:
            continue
        blocks, parent = _blocks(event), event.get("parent_tool_use_id")
        if blocks is None or not (parent is None or isinstance(parent, str)):
            unreadable += 1
            continue
        for block in blocks:
            if block["type"] == "tool_use":
                use = _tool_use(block)
                unreadable += use is None
                uses += [(*use, parent)] if use else []
            elif block["type"] == "tool_result":
                result = _tool_result(block)
                unreadable += result is None
                failed.update([result] if result else [])
    names, callers = [], {}
    agents = {use_id: given.get("subagent_type") for use_id, called, given, _ in uses if called == "Agent"}
    for use_id, called, given, parent in uses:
        name = given.get(name_key)
        if called != tool or use_id not in failed:
            continue
        if not isinstance(name, str) or not name:
            # One of this tool's calls, and which skill or agent it names is unknown.
            unreadable += 1
        elif not failed[use_id]:
            names.append(name)
            callers.setdefault(name, []).append(_caller(parent, agents))
    pending = sum(use_id not in failed for use_id, *_ in uses)
    orphans = len(failed.keys() - {use_id for use_id, *_ in uses})
    by = {name: who[0] for name, who in callers.items() if None not in who}
    return _Calls(names, bool(uses), _claude_doubt(unparsed, unreadable, pending, orphans), by)


def _caller(parent: str | None, agents: Mapping[str, object]) -> str | None:
    """Who made a call: None for the main agent, else the subagent, named if its `Agent` call was read."""
    if parent is None:
        return None
    name = agents.get(parent)
    return f"the subagent {name}" if isinstance(name, str) and name else "a subagent"


def _claude_doubt(unparsed: int, unreadable: int, pending: int, orphans: int) -> str | None:
    """Why a call might be in a Claude Code run and not among those read, or None.

    The stream holds an event of a known type, or it wouldn't have been read as Claude Code's.

    Args:
        unparsed: other lines that aren't blank: not JSON, or an event of a type the captures don't have.
        unreadable: messages, tool calls and results not in the captured shape.
        pending: tool calls with no result in the stream, which may or may not have worked.
        orphans: results whose tool call the stream doesn't show.
    """
    if unparsed:
        return f"{unparsed} line(s) of the stream couldn't be read as events"
    if unreadable:
        return f"{unreadable} tool call(s) or message(s) couldn't be read"
    if pending:
        return f"{pending} tool call(s) have no result in the stream"
    if orphans:
        return f"{orphans} result(s) are of tool calls the stream doesn't show"
    return None


def _blocks(event: dict) -> list[dict] | None:
    """An `assistant` or `user` event's content blocks, or None if any isn't of a type captured for that event."""
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    allowed = CLAUDE_BLOCK_TYPES[event["type"]]
    return content if all(isinstance(block, dict) and block.get("type") in allowed for block in content) else None


def _tool_use(block: dict) -> tuple[str, str, dict[str, Any]] | None:
    """A `tool_use` block's id, tool and input, or None if it isn't in the captured shape."""
    use_id, tool, given = block.get("id"), block.get("name"), block.get("input")
    if not (isinstance(use_id, str) and use_id and isinstance(tool, str) and tool and isinstance(given, dict)):
        return None
    return use_id, tool, given


def _tool_result(block: dict) -> tuple[str, bool] | None:
    """A `tool_result` block's tool call id and whether it is an error, or None if it isn't in the captured shape.

    `is_error` was captured as true, as false and left out.
    """
    use_id, is_error = block.get("tool_use_id"), block.get("is_error", False)
    if not (isinstance(use_id, str) and use_id and isinstance(is_error, bool)):
        return None
    return use_id, is_error


def _kilo_calls(events: str, tool: str, name_key: str) -> _Calls:
    """Read Kilo's stream for `tool`'s completed calls, each named by `part.state.input[name_key]`."""
    names: list[str] = []
    unparsed = unreadable = hidden = subagents = 0
    known = tool_calls = False  # an event of a known type was read; a readable tool call was read
    in_step = False  # a tool_use event has come since the last step_start, readable or not
    for line in events.splitlines():
        event = _event(line)
        if event is None:
            unparsed += bool(line.strip())
            continue
        known = True
        if event["type"] == "step_start":
            in_step = False
        elif event["type"] == "step_finish":
            hidden += _ended_in_tool_calls(event) and not in_step
        if event["type"] != "tool_use":
            continue
        in_step = True
        call = _tool_call(event)
        if call is None:
            unreadable += 1
            continue
        called, status, given = call
        tool_calls = True
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
    return _Calls(names, tool_calls, doubt)


def _doubt(known: bool, unparsed: int, unreadable: int, hidden: int, subagents: int) -> str | None:
    """Why a call might be in the run and not among those read, or None.

    Args:
        known: True if the stream held an event of a known type.
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


def _event(line: str, types: tuple[str, ...] = EVENT_TYPES) -> dict | None:
    """The line as an event of one of `types`, Kilo's unless given, or None."""
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) and event.get("type") in types else None


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
    targets=("kilo", "claude-code"),
    proven="docs/results/2026-11-10-skills-agents.md",
)(skill_loaded)

check(
    "skill_not_loaded",
    params=SkillParams,
    judges="agent",
    labels=("not_loaded", "loaded", "inconclusive"),
    targets=("kilo", "claude-code"),
)(skill_not_loaded)

check(
    "subagent_delegated",
    params=AgentParams,
    judges="agent",
    labels=("delegated", "not_delegated", "inconclusive"),
    targets=("kilo", "claude-code"),
    proven="docs/results/2026-11-10-skills-agents.md",
)(subagent_delegated)

check(
    "subagent_not_delegated",
    params=AgentParams,
    judges="agent",
    labels=("not_delegated", "delegated", "inconclusive"),
    targets=("kilo", "claude-code"),
)(subagent_not_delegated)
