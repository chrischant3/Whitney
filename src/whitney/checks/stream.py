"""One reader of each harness's event stream: every tool call, in the order the stream shows it.

A check that judges what the agent did in order, such as ran_before, reads
the stream through tool_calls(), which gives each call's tool, input, whether
it ran, and, for a shell command, whether it passed. Built for Task 50 so
that later checks (Task 51's read_before_edit) share it; the older checks
still read the stream with their own readers in whitney.checks.skills and
whitney.checks.permissions, and this module uses those readers' small shape
helpers rather than copying them.

Kilo 7.8.18 (`kilo run --format json`; docs/spike-findings.md, "Phase 5:
confirmed fields"; fixtures under tests/data/kilo-events/):

- each call is a `tool_use` event whose `part.tool` names the tool and
  `part.state` holds `status` and `input`;
- `status` "completed" ran, and "error" was refused or failed before it ran
  (bash-deny-pattern, task-deny);
- a completed `bash` call's exit code is `part.state.metadata.exit`: 0, 1 in
  bash-nonzero-exit, 128 for a failed `git diff` in skill-named.

A subagent's own calls aren't in Kilo's stream, so a completed `task` call
is a doubt: the subagent may have made calls the stream doesn't show.

Claude Code 2.1.322 (`claude -p --output-format stream-json --verbose`;
docs/spike-findings.md, "Phase 5 Task 36" and "Phase 5 Task 37"; fixtures
under tests/data/claude-code-events/):

- each call is a `tool_use` block (`id`, `name`, `input`) in an `assistant`
  event, and its result a `tool_result` block with the call's id in a `user`
  event; a subagent's calls are there too, in events whose
  `parent_tool_use_id` is the call that started it (subagent-loads-skill);
- a result that isn't an error ran. A `Bash` call that ran and exited
  non-zero has an error result whose text starts with the line "Exit code <n>" (settings-allow-ran: 1; skill-forked: 127). Any other error result
  didn't run: a permission rule's refusal (settings-deny-held), a hook's
  (hook-pretooluse-deny) or a tool's own error.

The order is the order in which the stream lists the calls. Two calls made
in one step are taken in the order the step lists them, though the harness
may have run them together.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from whitney.checks.permissions import _words
from whitney.checks.skills import (
    CLAUDE_BLOCK_TYPES,
    CLAUDE_EVENT_TYPES,
    _blocks,
    _caller,
    _claude_doubt,
    _doubt,
    _ended_in_tool_calls,
    _event,
    _is_claude_codes,
    _tool_call,
    _tool_result,
    _tool_use,
)

# Each harness's shell tool, and the key of its input that holds the command.
SHELL = {"kilo": ("bash", "command"), "claude-code": ("Bash", "command")}
# How Claude Code's result of a Bash command that exited non-zero begins (settings-allow-ran, skill-forked):
# its own line, followed by the command's output. A command with no output may leave the line alone.
CLAUDE_EXIT = re.compile(r"Exit code (\d+)(?:\n|$)")


@dataclass(frozen=True)
class ToolCall:
    """One tool call the stream shows.

    Attributes:
        tool: the tool's name, as the harness gives it (`bash`, `Bash`, `read`, ...).
        input: the call's input.
        ran: True if the harness ran it; False if it was refused, or failed before it ran.
        passed: for a shell command that ran, True if it exited 0 and False
            if not; None for any other call, or one whose exit the stream doesn't show.
        exit_code: a shell command's exit code, where the stream gives it;
            Claude Code gives it only for a command that failed.
        by: who made the call: None for the main agent, else "the subagent
            <its name>" or "a subagent" (Claude Code only).
    """

    tool: str
    input: Mapping[str, Any]
    ran: bool
    passed: bool | None = None
    exit_code: int | None = None
    by: str | None = None


@dataclass(frozen=True)
class ToolCalls:
    """What a stream shows of a run's tool calls.

    Attributes:
        harness: "kilo" or "claude-code", read from the stream's first event.
        calls: the calls, in the order the stream lists them.
        doubt: why the run may hold a call that isn't in `calls`, or that
            `calls` may not show in full, or None if the stream was read in full.
    """

    harness: Literal["kilo", "claude-code"]
    calls: tuple[ToolCall, ...]
    doubt: str | None

    def shell(self) -> tuple[str, str]:
        """This harness's shell tool and the key of its input that holds the command (SHELL)."""
        return SHELL[self.harness]


def tool_calls(events: str) -> ToolCalls:
    """Every tool call in a run's stream, Kilo's or Claude Code's, in order, and whether the stream was read in full.

    Args:
        events: the harness's event stream, one JSON event a line.
    """
    if _is_claude_codes(events):
        return _claude_tool_calls(events)
    return _kilo_tool_calls(events)


def _kilo_tool_calls(events: str) -> ToolCalls:
    """tool_calls for Kilo's stream."""
    calls: list[ToolCall] = []
    unparsed = unreadable = hidden = subagents = 0
    known = in_step = False  # as in whitney.checks.skills._kilo_calls
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
        tool, status, given = call
        ran = status == "completed"
        subagents += tool == "task" and ran
        exit_code = _kilo_exit(event) if ran and tool == SHELL["kilo"][0] else None
        calls.append(ToolCall(tool, given, ran, None if exit_code is None else exit_code == 0, exit_code))
    return ToolCalls("kilo", tuple(calls), _doubt(known, unparsed, unreadable, hidden, subagents))


def _kilo_exit(event: dict) -> int | None:
    """A completed bash call's `part.state.metadata.exit`, or None if it isn't a whole number."""
    metadata = event["part"]["state"].get("metadata")
    exit_code = metadata.get("exit") if isinstance(metadata, dict) else None
    # bool is an int, and `true` isn't an exit code.
    return exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None


def _claude_tool_calls(events: str) -> ToolCalls:
    """tool_calls for Claude Code's stream. A call with no result in the stream is left out, and is a doubt."""
    uses: list[tuple[str, str, dict[str, Any], str | None]] = []  # each call: id, tool, input, parent call's id
    results: dict[str, str | None] = {}  # a result's call id -> its text if it is an error, else None
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
                if result:
                    results[result[0]] = _words(block.get("content")) if result[1] else None
    agents = {use_id: given.get("subagent_type") for use_id, tool, given, _ in uses if tool == "Agent"}
    calls = tuple(
        _claude_call(tool, given, results[use_id], _caller(parent, agents))
        for use_id, tool, given, parent in uses
        if use_id in results
    )
    pending = sum(use_id not in results for use_id, *_ in uses)
    orphans = len(results.keys() - {use_id for use_id, *_ in uses})
    return ToolCalls("claude-code", calls, _claude_doubt(unparsed, unreadable, pending, orphans))


def _claude_call(tool: str, given: dict[str, Any], error: str | None, by: str | None) -> ToolCall:
    """One Claude Code call, from its result's text if that is an error (`error`), else None."""
    shell = tool == SHELL["claude-code"][0]
    if error is None:
        return ToolCall(tool, given, True, True if shell else None, None, by)
    exited = CLAUDE_EXIT.match(error) if shell else None
    if exited is None:
        return ToolCall(tool, given, False, None, None, by)
    return ToolCall(tool, given, True, False, int(exited.group(1)), by)
