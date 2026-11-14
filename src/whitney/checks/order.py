"""The check ran_before: a shell command matching `then` ran only after one matching `first`.

For a rule such as "run the tests before committing": `first` matches the
test command and `then` the commit. It reads every tool call in order through
whitney.checks.stream, which names the fields it reads on each harness, and
matches only the shell tool's commands (Kilo's `bash`, Claude Code's `Bash`,
`input.command` on both).

Only a call that ran counts, on either side: a refused commit committed
nothing, and refused tests tested nothing. A command that ran and failed did
run, so a failed `git commit` is still a commit made before the tests, and a
failed test run still counts as `first` unless `first_must_pass` is set.
Both harnesses show a command's exit: Kilo in `metadata.exit` (fixture
bash-nonzero-exit), Claude Code as an error result starting "Exit code <n>"
(fixtures settings-allow-ran, skill-forked).

Only the first commit that ran is judged: if it came after the tests, every
later one did too. A command that runs both, such as `pytest && git commit`,
counts as in order when the `first` match comes before the `then` match in
its text; under `first_must_pass` it can't show the tests passed, since its
exit is the whole command's.
Nor, under `first_must_pass`, can a test run followed in its command by a
pipe, `||`, `;` or a lone `&` (MASKING): `pytest | tail` exits as `tail` does.
Patterns match anywhere in a command's text, so an eval file should anchor
them where a command starts (docs/adding-tests.md, "`ran_before`").

On Kilo a subagent's own calls aren't in the stream, so a run that delegated
with `task` is inconclusive whatever order the stream shows, as is any run
whose stream may hide or mis-show a call (whitney.checks.stream). On Claude
Code a subagent's calls are in the stream and count, in the order it lists them.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from whitney.checks import Evidence, Verdict, _listed, check
from whitney.checks.stream import SHOWN, ToolCall, tool_calls

# A shell operator after the tests' command, which can make the command's exit another command's:
# a pipe or `||`, `;`, or `&` on its own (not `&&`, and not a redirection such as `2>&1` or `&>`).
MASKING = re.compile(r"[|;]|(?<![&>])&(?![&>])")
# Characters kept before the match, so a long `cd ... && git commit` shows the commit and what led to it.
LEAD = 20


class Params(BaseModel):
    """ran_before's parameters.

    Attributes:
        first: a regular expression for the command that must come first,
            such as `\\bpytest\\b`, searched in each shell command.
        then: a regular expression for the command that must come after it,
            such as `\\bgit\\s+commit\\b`.
        first_must_pass: if true, only a `first` command that exited 0
            counts, so a failing test run before a commit doesn't.
    """

    model_config = ConfigDict(extra="forbid")

    first: str = Field(min_length=1)
    then: str = Field(min_length=1)
    first_must_pass: bool = False

    @field_validator("first", "then")
    @classmethod
    def _a_pattern_that_can_miss(cls, value: str) -> str:
        try:
            pattern = re.compile(value)
        except re.error as e:
            raise ValueError(f"isn't a regular expression: {e}") from e
        if pattern.search("") is not None:
            raise ValueError("matches an empty command, so it would match every command")
        return value


def ran_before(evidence: Evidence, params: Params) -> Verdict:
    """Did every command matching `then` come after one matching `first`?

    Fail if the first `then` command that ran had no `first` command that
    ran (and, under first_must_pass, exited 0) before it. Pass if it had one.
    Otherwise inconclusive: no `then` command ran, the stream may hide or
    mis-show a call (a subagent on Kilo included), a shell call has no
    command, or, under first_must_pass, the stream doesn't show whether the
    `first` before it passed. An early end keeps a pass from standing
    (CheckSpec.run).

    Reads: evidence.events, through whitney.checks.stream.tool_calls.
    """
    read = tool_calls(evidence.events)
    tool, key = read.shell()
    first, then = re.compile(params.first), re.compile(params.then)
    commands = [(n, call, call.input.get(key)) for n, call in enumerate(read.calls, 1) if call.tool == tool]
    named = [(n, call, command) for n, call, command in commands if isinstance(command, str) and command]
    firsts = [(n, call, command) for n, call, command in named if call.ran and first.search(command)]
    thens = [(n, call, command) for n, call, command in named if then.search(command)]
    details = {
        "first": [_shown(entry, first) for entry in firsts],
        "then": [_shown(entry, then) for entry in thens if entry[1].ran],
    }
    verdict = _ordered(firsts, thens, first, then, params.first_must_pass)
    doubt = read.doubt
    if doubt is None and len(named) < len(commands):
        doubt = f"{len(commands) - len(named)} shell call(s) have no command"
    if doubt:
        return Verdict("inconclusive", f"{verdict.explanation}, but {doubt}", details=details)
    return Verdict(verdict.outcome, verdict.explanation, details=details)


Entry = tuple[int, ToolCall, str]  # a shell call's place among the stream's calls, the call and its command


def _ordered(firsts: list[Entry], thens: list[Entry], first: re.Pattern, then: re.Pattern, must_pass: bool) -> Verdict:
    """The verdict from the calls alone: the `first` calls that ran and every `then` call, in order."""
    ran = [entry for entry in thens if entry[1].ran]
    if not ran:
        refused = len(thens)
        return Verdict("inconclusive", "no call matching then ran" + (f"; {refused} call(s) matching then were refused" if refused else ""))
    judged = ran[0]
    shown = _shown(judged, then)
    before = [entry for entry in firsts if entry[0] < judged[0]]
    after = [entry for entry in firsts if entry[0] > judged[0]]
    together = _first_inside(first, then, judged[2])
    passed = [entry for entry in before if entry[1].passed and _unsure(entry, first) is None] if must_pass else before
    if passed:
        return Verdict("pass", f"{_shown(passed[-1], first)} {'passed' if must_pass else 'ran'} before {shown}")
    if together and not must_pass:
        return Verdict("pass", _inside(judged, first))
    unknown = [entry for entry in before if _unsure(entry, first)]
    if unknown:
        return Verdict("inconclusive", f"{shown} ran after {_shown(unknown[-1], first)}, {_unsure(unknown[-1], first)}")
    if together:
        return Verdict("inconclusive", f"{_inside(judged, first)}, and its exit can't say whether that part passed")
    if before:
        failed = _listed([_shown(entry, first) for entry in before])
        return Verdict("fail", f"{shown} ran, and no call matching first passed before it: {failed}")
    later = f"; the first came after it: {_shown(after[0], first)}" if after else ""
    return Verdict("fail", f"{shown} ran with no call matching first before it{later}")


def _unsure(entry: Entry, first: re.Pattern) -> str | None:
    """Why a `first` call's exit can't show it passed, or None if it can.

    After the match, a pipe, `||`, `;` or a lone `&` can make the command's
    exit another command's: `pytest | tail` exits as `tail` does.
    """
    _, call, command = entry
    if MASKING.search(command, first.search(command).end()):
        return "whose exit may be another command's, after a |, ; or & that follows it"
    if call.passed is None:
        return "whose exit the stream doesn't show"
    return None


def _inside(judged: Entry, first: re.Pattern) -> str:
    """The explanation's words for a command that runs both, `first`'s part first."""
    return f"{_shown(judged, first)} runs a command matching first before the one matching then"


def _first_inside(first: re.Pattern, then: re.Pattern, command: str) -> bool:
    """True if `command`, which `then` matches, also matches `first`, and earlier in its text."""
    found = first.search(command)
    return found is not None and found.start() < then.search(command).start()


def _shown(entry: Entry, pattern: re.Pattern) -> str:
    """A call as an explanation names it: its command, its place, its exit and who made it.

    The command, which `pattern` matches, is cut to SHOWN characters,
    starting LEAD characters before the match, with "…" where it was cut.
    """
    n, call, command = entry
    start = max(0, pattern.search(command).start() - LEAD)
    cut = ("…" if start else "") + command[start:start + SHOWN] + ("…" if start + SHOWN < len(command) else "")
    exit_code = "" if call.exit_code is None else f", exit {call.exit_code}"
    by = f", by {call.by}" if call.by else ""
    return f"`{cut}` (call {n}{exit_code}{by})"


check(
    "ran_before",
    params=Params,
    judges="agent",
    labels=("in_order", "out_of_order", "inconclusive"),
    targets=("kilo", "claude-code"),
    proven="docs/results/2026-11-13-ran-before.md",
)(ran_before)
