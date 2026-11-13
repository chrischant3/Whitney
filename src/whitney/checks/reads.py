"""The check read_before_edit: a file matching `file` was read before the agent's first edit.

For a rule such as "read CONSTRAINTS.md before writing code": `file` matches
the file to read, and `edits`, if given, the files whose edits are judged.
It reads every tool call in order through whitney.checks.stream, which names
the fields it reads on each harness.

What counts as a read (docs/adding-tests.md, "`read_before_edit`"):

- the harness's read tool, Kilo's `read` (`input.filePath`) or Claude Code's
  `Read` (`input.file_path`), on a path `file` matches. With `offset` or
  `limit` set it is a partial read;
- a shell command (Kilo's `bash`, Claude Code's `Bash`) one of whose simple
  commands is a reader in READERS with a file argument `file` matches, and
  whose output the agent saw: not redirected, not inside `$(...)`, and not
  piped into a program other than those in PIPED_INTO. `cat`, `less` and
  `more` read the whole file, `head`, `tail` and `sed -n` part of it. A shell
  read whose exit was non-zero, or may be another command's (_masked), may not
  have read the file, so it makes neither a pass nor a fail.

A partial read counts unless `whole_file` is set. A search, `grep` or `rg` in
the shell or Kilo's `grep` and `glob` tools, isn't a read: it shows a few
lines, or only names. A read made in the same step (Kilo) or turn (Claude
Code) as the edit doesn't count as before it, and makes the run fail: the
edit was written before the read's result came back.

What counts as an edit: the harness's edit tools in EDIT_TOOLS, on a path
`edits` matches (any path if `edits` isn't given). A shell command can write
too, but which file it wrote can't be read reliably, so one that may write
(_writes, and redirects into a file) before the first read keeps a pass from
standing, and never makes a fail. Only calls that ran count, on either side.

On Kilo a subagent's own calls aren't in the stream, so a run that delegated
with `task` is inconclusive whatever order the stream shows, as is any run
whose stream may hide or mis-show a call (whitney.checks.stream), or that ran
a tool that runs shell commands Whitney doesn't read (UNSEEN_SHELLS). On
Claude Code a subagent's calls are in the stream and count.
"""

import os
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from whitney.checks import Evidence, Verdict, _listed, check
from whitney.checks.stream import SHOWN, ToolCall, ToolCalls, tool_calls

# Each harness's read tool and edit tools, and the key of their input that holds the file's path. Kilo 7.8.27's
# tools (tests/data/kilo-events/tools-default.debug.json) and Claude Code 2.1.322's (its init event's `tools`).
READ_TOOLS = {"kilo": {"read": "filePath"}, "claude-code": {"Read": "file_path"}}
EDIT_TOOLS = {
    "kilo": {"edit": "filePath", "write": "filePath"},
    "claude-code": {"Edit": "file_path", "Write": "file_path", "NotebookEdit": "notebook_path"},
}
# Tools in the same lists that run shell commands whose text Whitney doesn't read.
UNSEEN_SHELLS = {"kilo": ("background_process",), "claude-code": ("Monitor",)}
# Shell readers, and whether each reads the whole file. `sed` reads only with -n, and never with -i.
READERS = {"cat": True, "less": True, "more": True, "head": False, "tail": False, "sed": False}
# What a reader's output can be piped into and still be seen, and whether all of it: a pager, or head or tail.
PIPED_INTO = {"cat": True, "less": True, "more": True, "head": False, "tail": False}
# head's and tail's options that take a value, which isn't a file.
VALUED = frozenset({"-n", "-c"})
# Shell commands taken as writing a file, whatever their arguments.
WRITERS = frozenset({"tee", "cp", "mv", "rm", "touch", "patch", "truncate", "dd", "install", "ln"})
# git subcommands that change the working tree.
GIT_WRITES = frozenset({
    "apply", "am", "checkout", "restore", "switch", "reset", "stash", "merge", "rebase", "pull", "cherry-pick", "mv", "rm",
    "clean", "revert",
})
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
# Words that start a simple command without being its program, and words that end a compound command and run nothing.
KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"})
CLOSERS = frozenset({"fi", "done", "}", "esac"})
# Programs that run the program named after them, and their options that take a value.
WRAPPERS = {"sudo": {"-u", "-g"}, "env": {"-u"}, "command": set(), "nohup": set(), "exec": set(), "xargs": {"-I", "-n", "-L", "-P", "-d"}}
# The shell's separators, as shlex runs them together (`);`, say) and as they are taken apart again.
SEPARATOR_CHARACTERS = frozenset(";&|()")
OPERATOR = re.compile(r"\|\||&&|;;|\|&|[;&|()]")
WRITE_REDIRECTS = frozenset({">", ">>", ">|"})
BOTH_REDIRECTS = frozenset({"&>", "&>>"})
SKIPPED_REDIRECTS = frozenset({"<&", "<<<", "<>", "<<"})  # each takes a word that is neither read nor written
HEREDOCS = frozenset({"<<"})
REDIRECTS = WRITE_REDIRECTS | BOTH_REDIRECTS | SKIPPED_REDIRECTS | {"<", ">&"}
# A line ending in a `\` that isn't itself escaped, so the next line goes on with it.
CONTINUED = re.compile(r"(?<!\\)(\\\\)*\\$")


class Params(BaseModel):
    """read_before_edit's parameters.

    Attributes:
        file: a regular expression for the path of the file that must be read
            first, such as `(^|/)CONSTRAINTS\\.md$`, searched in each path read.
        edits: a regular expression for the paths whose edits are judged, such
            as `\\.py$`; left out, every edit.
        whole_file: if true, a partial read (an offset or limit, `head`,
            `tail`, `sed -n`) doesn't count.
    """

    model_config = ConfigDict(extra="forbid")

    file: str = Field(min_length=1)
    edits: str | None = None
    whole_file: bool = False

    @field_validator("file", "edits")
    @classmethod
    def _a_pattern_that_can_miss(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            pattern = re.compile(value)
        except re.error as e:
            raise ValueError(f"isn't a regular expression: {e}") from e
        if pattern.search("") is not None:
            raise ValueError("matches an empty path, so it would match every file")
        return value


@dataclass(frozen=True)
class Act:
    """A read, an edit or a shell write the check counted.

    Attributes:
        n: its call's place among the stream's calls, from 1.
        shown: how an explanation names it.
        step: the Kilo step or Claude Code turn its call was made in, if the stream shows it.
        partial: a read of part of the file.
        unsure: why a shell read may not have read the file, or None.
    """

    n: int
    shown: str
    step: int | str | None = None
    partial: bool = False
    unsure: str | None = None
    at: int = 0  # within a shell command, the place of the simple command that read or wrote


@dataclass
class Seen:
    """What the check gathered from a run's calls, each list in the stream's order."""

    reads: list[Act] = field(default_factory=list)
    edits: list[Act] = field(default_factory=list)
    writes: list[Act] = field(default_factory=list)
    not_run: int = 0
    no_command: int = 0
    no_path: int = 0
    unseen: list[str] = field(default_factory=list)


def read_before_edit(evidence: Evidence, params: Params) -> Verdict:
    """Was a file matching `file` read before the first edit?

    Fail if the first edit that ran (of a path `edits` matches) had no read
    of the file in an earlier step or turn: none before it, or only one made in
    its own step. Pass if it had one. Otherwise inconclusive: no edit ran, the
    only reads before it were shell reads that may have failed, a shell command
    before the read may have written a file, the stream may hide or mis-show
    a call (a subagent on Kilo included), or a read, edit or shell call has no
    path or command. An early end keeps a pass from standing (CheckSpec.run).

    Reads: evidence.events, through whitney.checks.stream.tool_calls.
    """
    stream = tool_calls(evidence.events)
    seen = _gather(stream, re.compile(params.file), re.compile(params.edits) if params.edits is not None else None)
    details = {"reads": [act.shown for act in seen.reads], "edits": [act.shown for act in seen.edits]}
    verdict = _ordered(seen, params)
    doubt = _doubt(stream, seen)
    if doubt:
        return Verdict("inconclusive", f"{verdict.explanation}, but {doubt}", details=details)
    return Verdict(verdict.outcome, verdict.explanation, details=details)


def _gather(stream: ToolCalls, file: re.Pattern, edited: re.Pattern | None) -> Seen:
    """The reads of `file`, the edits `edited` matches, the shell writes and the calls that couldn't be read."""
    seen = Seen()
    shell_tool, key = stream.shell()
    reads, edits = READ_TOOLS[stream.harness], EDIT_TOOLS[stream.harness]
    for n, call in enumerate(stream.calls, 1):
        if call.tool == shell_tool:
            _shell_call(seen, n, call, call.input.get(key), file)
        elif call.tool in reads:
            path = call.input.get(reads[call.tool])
            if not _a_path(path):
                seen.no_path += 1
            elif call.ran and file.search(path):
                partial = _partial(call.input)
                seen.reads.append(Act(n, _named(n, call, path, partial), _step(call), partial))
        elif call.tool in edits:
            path = call.input.get(edits[call.tool])
            if not _a_path(path):
                seen.no_path += 1
            elif edited is not None and not edited.search(path):
                continue
            elif call.ran:
                seen.edits.append(Act(n, _named(n, call, path), _step(call)))
            else:
                seen.not_run += 1
        elif call.tool in UNSEEN_SHELLS[stream.harness] and call.ran:
            seen.unseen.append(call.tool)
    return seen


def _shell_call(seen: Seen, n: int, call: ToolCall, command: Any, file: re.Pattern) -> None:
    """Add a shell call's read of `file`, if any, and its first write, if any, to `seen`."""
    if not (isinstance(command, str) and command):
        seen.no_command += 1
        return
    if not call.ran:
        return
    done = shell_files(command)
    matched = [reading for reading in done.reads if file.search(reading.path)]
    if matched:
        partial = not any(reading.whole for reading in matched)
        unsure = None
        if call.passed is False:
            unsure = "which exited non-zero"
        elif all(reading.masked for reading in matched):
            unsure = "whose exit may be another command's"
        seen.reads.append(Act(n, _command(n, call, command, partial), _step(call), partial, unsure, matched[0].at))
    if done.writes:
        seen.writes.append(Act(n, _command(n, call, command), at=done.writes[0]))


def _ordered(seen: Seen, params: Params) -> Verdict:
    """The verdict from the calls alone."""
    whole_file = params.whole_file
    if not seen.edits:
        words = "no edit ran" if params.edits is None else "no edit matching edits ran"
        words += f"; {seen.not_run} edit call(s) didn't run" if seen.not_run else ""
        words += f"; {len(seen.writes)} shell command(s) may have written a file" if seen.writes else ""
        return Verdict("inconclusive", words)
    judged = seen.edits[0]
    counted = [act for act in seen.reads if not (whole_file and act.partial)]
    before = [act for act in counted if act.n < judged.n]
    same_step = [act for act in before if act.step is not None and act.step == judged.step]
    kept = [act for act in before if act not in same_step]  # reads whose result came back before the edit was asked for
    earlier = [act for act in kept if act.unsure is None]
    if earlier:
        said = f"{earlier[-1].shown} came before {judged.shown}"
        risky = [act for act in seen.writes if (act.n, act.at) < (earlier[0].n, earlier[0].at)]
        if risky:
            return Verdict("inconclusive", f"{said}, but a shell command before the read may have written a file: {risky[0].shown}")
        return Verdict("pass", said)
    if kept:
        doubtful = kept[-1]
        return Verdict("inconclusive", f"{judged.shown} ran after {doubtful.shown}, {doubtful.unsure}, so it may not have read the file")
    if same_step:
        # The agent asked for the edit before the read's result came back, so it edited without having seen the file
        # (Christopher Chant, 2026-11-13).
        return Verdict(
            "fail",
            f"{judged.shown} ran with no read of a file matching file before it; {same_step[-1].shown} came in the same step "
            "as the edit, so the edit was written before the read's result came back",
        )
    partial = [act.shown for act in seen.reads if act.n < judged.n]
    if partial:
        return Verdict("fail", f"{judged.shown} ran with no whole read of a file matching file before it; partial: {_listed(partial)}")
    after = [act for act in counted if act.n > judged.n and act.unsure is None]
    later = f"; the read came after it: {after[0].shown}" if after else ""
    return Verdict("fail", f"{judged.shown} ran with no read of a file matching file before it{later}")


def _doubt(stream: ToolCalls, seen: Seen) -> str | None:
    """Why the calls the check gathered may not show the run in full, or None."""
    if stream.doubt:
        return stream.doubt
    if seen.no_command:
        return f"{seen.no_command} shell call(s) have no command"
    if seen.no_path:
        return f"{seen.no_path} read or edit call(s) have no file path"
    if seen.unseen:
        return f"{len(seen.unseen)} call(s) to a tool that runs shell commands Whitney doesn't read: {_listed(seen.unseen)}"
    return None


# --- Shell commands ----------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """A file a shell command's reader read, and how.

    Attributes:
        path: the file, as the command names it.
        whole: True if the agent saw the whole file.
        at: the place of the reader's simple command in the command, from 0.
        masked: True if a separator after it makes the command's exit another's (_masked).
    """

    path: str
    whole: bool
    at: int
    masked: bool


@dataclass(frozen=True)
class Shell:
    """What one shell command does to files: the files its readers read, and the places of the simple commands that may write one."""

    reads: tuple[Reading, ...]
    writes: tuple[int, ...]


@dataclass
class Simple:
    """One simple command, as _simple_commands cuts it out.

    Attributes:
        words: its words as written, assignments, keywords and wrappers included.
        inputs: files given to it with `<`.
        hidden: True if its output went somewhere other than the agent: a
            redirect of its standard output, or inside `$(...)`.
        writes: True if a redirect of it writes a file.
        after: the separator that ends it: an operator, "$(" or a new line.
    """

    words: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    hidden: bool = False
    writes: bool = False
    after: str = "\n"

    def program(self) -> tuple[str, list[str]]:
        """The program it runs and that program's arguments: its words with assignments, keywords and wrappers taken off."""
        words = self.words
        while words:
            first, words = words[0], words[1:]
            if first in WRAPPERS:
                while words and words[0].startswith("-"):
                    words = words[2:] if words[0] in WRAPPERS[first] else words[1:]
            elif not (ASSIGNMENT.fullmatch(first) or first in KEYWORDS):
                return os.path.basename(first), words
        return "", []


def shell_files(command: str) -> Shell:
    """The files a shell command's readers read, and where it may write one.

    The command is cut into logical lines (a line ending in `\\` goes on, and
    so does a quote left open), each split as the shell would (shlex), and
    each simple command read on its own. A heredoc's body is skipped. A quote
    left open to the end is split line by line on spaces.
    """
    simples = [simple for tokens in _logical_lines(command) for simple in _simple_commands(tokens)]
    programs = [simple.program() for simple in simples]
    reads: list[Reading] = []
    writes: list[int] = []
    for at, (simple, (name, arguments)) in enumerate(zip(simples, programs)):
        piped = programs[at + 1][0] if simple.after in ("|", "|&") and at + 1 < len(simples) else None
        if not simple.hidden and (piped is None or piped in PIPED_INTO):
            whole = PIPED_INTO.get(piped, True)
            masked = _masked(simples, programs, at)
            reads += [Reading(path, whole and full, at, masked) for path, full in _reads(name, arguments, simple.inputs)]
        if simple.writes or _writes(name, arguments):
            writes.append(at)
    return Shell(tuple(reads), tuple(writes))


def _logical_lines(command: str) -> list[list[str]]:
    """The command's logical lines, each as tokens, with heredoc bodies left out."""
    found: list[list[str]] = []
    pending = ""  # the start of a logical line still going on
    ending: str | None = None
    for line in command.split("\n"):
        if ending is not None:
            ending = None if line.strip() == ending else ending
            continue
        text = pending + line
        if CONTINUED.search(text):
            pending = text[:-1]
            continue
        tokens = _split(text)
        if tokens is None:
            pending = text + "\n"
            continue
        pending = ""
        found.append(tokens)
        ending = _heredoc(tokens)
    if pending:
        found += [line.split() for line in pending.split("\n")]
    return found


def _split(text: str) -> list[str] | None:
    """`text`'s words and operators, as shlex splits them, or None if a quote is left open."""
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:
        return None


def _heredoc(tokens: list[str]) -> str | None:
    """The delimiter of the last heredoc a line starts, whose body follows on the next lines, or None.

    shlex splits `<<-EOF` as `<<` and `-EOF`, so a delimiter starting with `-` loses it.
    """
    ending = None
    for token, following in zip(tokens, tokens[1:]):
        if token in HEREDOCS:
            ending = following.removeprefix("-")
    return ending


def _simple_commands(tokens: list[str]) -> list[Simple]:
    """A logical line's tokens cut into simple commands, each with its redirects read and its separator kept."""
    commands = [Simple()]
    depth = 0  # how deep in `$(...)` the tokens are
    at = 0
    while at < len(tokens):
        token = tokens[at]
        if set(token) <= SEPARATOR_CHARACTERS:
            # shlex splits `$(` into a word ending in `$` and a `(`, and runs separators together, such as `);`.
            opens = at > 0 and tokens[at - 1].endswith("$")
            for operator in OPERATOR.findall(token):
                if operator == "(" and opens:
                    depth += 1
                elif operator == ")" and depth:
                    depth -= 1
                opens = False
                commands[-1].after = operator
                commands.append(Simple(hidden=depth > 0))
        else:
            at = _word(commands[-1], tokens, at)
        at += 1
    return [simple for simple in commands if simple.words]


def _word(simple: Simple, tokens: list[str], at: int) -> int:
    """Add tokens[at], a word or a redirect, to `simple`; returns the place of the last token it used.

    A redirect with nothing after it, which the shell refuses to run, is left out.
    """
    token = tokens[at]
    if token not in REDIRECTS:
        simple.words.append(token)
        return at
    if at + 1 == len(tokens):
        return at
    target = tokens[at + 1]
    if token in WRITE_REDIRECTS or token == ">&":
        descriptor = simple.words.pop() if simple.words and simple.words[-1].isdigit() else "1"  # the 2 of `2>`
        duplicate = token == ">&" and (target.isdigit() or target == "-")
        simple.hidden = simple.hidden or (descriptor == "1" and not duplicate)
        simple.writes = simple.writes or (not duplicate and target != "/dev/null")
    elif token in BOTH_REDIRECTS:
        simple.hidden = True
        simple.writes = simple.writes or target != "/dev/null"
    elif token == "<":
        simple.inputs.append(target)
    return at + 1


def _masked(simples: list[Simple], programs: list[tuple[str, list[str]]], at: int) -> bool:
    """True if a separator after simples[at] makes the whole command's exit another command's.

    `||` and `&` always do. `;` and a new line do when another command, not a
    closing word (CLOSERS), follows: `cat X; echo done`, not `{ cat X; }` or
    `cat X; fi`. `&&` doesn't, since exit 0 then means every part passed, and
    nor does a pipe (docs/adding-tests.md).
    """
    for place in range(at, len(simples)):
        after = simples[place].after
        if after in ("||", "&"):
            return True
        if after in (";", "\n") and place + 1 < len(simples) and programs[place + 1][0] not in CLOSERS:
            return True
    return False


def _reads(name: str, arguments: list[str], inputs: list[str]) -> list[tuple[str, bool]]:
    """The files a command named `name` reads, each with True if whole: its file arguments and `<` inputs, if it is a reader."""
    if name not in READERS:
        return []
    if name == "sed":
        if not _quiet_sed([word for word in arguments if word.startswith("-")]):
            return []
        files = _sed_files(arguments)
    else:
        files = _files(arguments, VALUED if name in ("head", "tail") else frozenset())
    return [(path, READERS[name]) for path in files + inputs if path]


def _files(arguments: list[str], valued: frozenset[str]) -> list[str]:
    """A reader's file arguments: what isn't an option, nor the value of an option in `valued`."""
    files: list[str] = []
    words = iter(arguments)
    for word in words:
        if word in valued:
            next(words, None)
        elif not word.startswith("-"):
            files.append(word)
    return files


def _quiet_sed(flags: list[str]) -> bool:
    """True if sed's options print only what its script asks for (-n) and don't edit in place (-i)."""
    quiet = _short(flags, "n") or "--quiet" in flags or "--silent" in flags
    return quiet and not _short(flags, "i") and not any(flag.startswith("--in-place") for flag in flags)


def _sed_files(arguments: list[str]) -> list[str]:
    """sed's file arguments: what isn't an option, its script, or the value of -e, -f, --expression or --file."""
    files: list[str] = []
    scripted = False
    words = iter(arguments)
    for word in words:
        if word in ("--expression", "--file") or (not word.startswith("--") and word.startswith("-") and word[-1] in "ef"):
            next(words, None)
            scripted = True
        elif word.startswith(("--expression=", "--file=")):
            scripted = True
        elif word.startswith("-"):
            continue
        elif scripted:
            files.append(word)
        else:
            scripted = True  # the first word that isn't an option is the script
    return files


def _writes(name: str, arguments: list[str]) -> bool:
    """True if a command named `name`, with these arguments, may write a file (WRITERS, sed, perl or awk in place, GIT_WRITES)."""
    if name in WRITERS:
        return True
    if name == "sed":
        return _short(arguments, "i") or any(word.startswith("--in-place") for word in arguments)
    if name == "perl":
        return _short(arguments, "i")
    if name in ("awk", "gawk"):
        return any(word == "-i" and value == "inplace" for word, value in zip(arguments, arguments[1:]))
    if name == "git":
        return _git_subcommand(arguments) in GIT_WRITES
    return False


def _short(words: list[str], letter: str) -> bool:
    """True if one of `words` is a short option, or a group of them such as `-ni`, holding `letter`."""
    return any(word[:1] == "-" and word[1:2] != "-" and letter in word[1:] for word in words)


def _git_subcommand(arguments: list[str]) -> str | None:
    """git's subcommand: the first argument that isn't an option, nor the value of -C or -c."""
    words = iter(arguments)
    for word in words:
        if word in ("-C", "-c"):
            next(words, None)
        elif not word.startswith("-"):
            return word
    return None


# --- How an explanation names a call --------------------------------------------------------


def _a_path(path: Any) -> bool:
    """True if a read or edit tool's path is a string that isn't empty."""
    return isinstance(path, str) and bool(path)


def _partial(given: Mapping[str, Any]) -> bool:
    """True if a read tool's input sets an offset or a limit."""
    return given.get("offset") is not None or given.get("limit") is not None


def _step(call: ToolCall) -> int | str | None:
    """The Kilo step or Claude Code turn a call was made in, or None if the stream doesn't show it."""
    return call.step if call.step is not None else call.turn


def _named(n: int, call: ToolCall, path: str, partial: bool = False) -> str:
    """A read or edit tool's call as an explanation names it: the tool, the path (its end, if long), its place and who made it."""
    cut = path if len(path) <= SHOWN else "…" + path[-SHOWN:]
    return f"{call.tool} of `{cut}` (call {n}{', partial' if partial else ''}{_by(call)})"


def _command(n: int, call: ToolCall, command: str, partial: bool = False) -> str:
    """A shell call as an explanation names it: the command (its start, if long), its place, its exit and who made it."""
    cut = command if len(command) <= SHOWN else command[:SHOWN] + "…"
    exit_code = "" if call.exit_code is None else f", exit {call.exit_code}"
    return f"`{cut}` (call {n}{exit_code}{', partial' if partial else ''}{_by(call)})"


def _by(call: ToolCall) -> str:
    """", by <who>" for a subagent's call, else nothing."""
    return f", by {call.by}" if call.by else ""


check(
    "read_before_edit",
    params=Params,
    judges="agent",
    labels=("read_first", "edited_unread", "inconclusive"),
    targets=("kilo", "claude-code"),
    proven="docs/results/2026-11-13-read-before-edit.md",
)(read_before_edit)
