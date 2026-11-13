"""The check read_before_edit: a file matching `file` was read before the first edit.

It is judged on streams Kilo 7.8.27 and Claude Code 2.1.322 really printed
(tests/data/kilo-events/ and tests/data/claude-code-events/), and on streams
built from their events, with only the tools, inputs and results changed, to
show what a different run would print.
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from whitney.checks import Evidence, registry

DATA = Path(__file__).parent / "data"
PROJECT = "/tmp/kilo-eval-XXXXXXXX/project"
RULES = f"{PROJECT}/CONSTRAINTS.md"
CODE = f"{PROJECT}/till.py"
READ_RULES = {"file": r"(^|/)CONSTRAINTS\.md$"}


def lines(harness: str, case: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / f"{harness}-events" / f"{case}.jsonl").read_text().splitlines()]


def text(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def read(path: str, **more) -> tuple:
    return ("read", {"filePath": path, **more})


def edit(path: str = CODE) -> tuple:
    return ("edit", {"filePath": path, "oldString": "a", "newString": "b"})


def write(path: str = CODE) -> tuple:
    return ("write", {"filePath": path, "content": "x"})


def bash(command: str, exit_code: int = 0) -> tuple:
    return ("bash", {"command": command}, exit_code)


def kilo(*calls) -> str:
    """A Kilo stream of one step per call, each (tool, input), (tool, input, "error") for a call that didn't
    run, or ("bash", input, exit code). Built from tests-then-commit's events: its step around the read, once
    per call, then its last step."""
    events = lines("kilo", "tests-then-commit")
    start, words, use, finish = events[:4]
    shell = next(event for event in events if event["type"] == "tool_use" and event["part"]["tool"] == "bash")
    made = []
    for tool, given, *outcome in calls:
        call = copy.deepcopy(shell if tool == "bash" else use)
        call["part"]["tool"] = tool
        state = call["part"]["state"]
        state["input"] = given
        if outcome == ["error"]:
            call["part"]["state"] = {"status": "error", "input": given, "error": "refused", "time": state["time"]}
        elif tool == "bash":
            state["metadata"]["exit"] = outcome[0]
        made += [start, words, call, finish]
    return text(made + events[-3:])


def claude(*calls, by: str | None = None) -> str:
    """A Claude Code stream of one call and result per call, each in a turn of its own, each (tool, input) or (tool, input, result), the
    result being "ok" (the default), "error" or, for Bash, an exit code. Built from settings-allow-ran's events.
    `by`: the parent call's id."""
    events = lines("claude-code", "settings-allow-ran")
    use_at = next(i for i, e in enumerate(events) if e["type"] == "assistant" and "tool_use" in json.dumps(e["message"]["content"]))
    use, result = events[use_at], events[use_at + 1]
    made = []
    for n, (tool, given, *outcome) in enumerate(calls):
        call, answer = copy.deepcopy(use), copy.deepcopy(result)
        call_id = f"toolu_{n}"
        call["message"]["content"][0].update(id=call_id, name=tool, input=given)
        call["message"]["id"] = f"msg_{n}"  # each call in a turn of its own
        block = answer["message"]["content"][0]
        block["tool_use_id"] = call_id
        said = outcome[0] if outcome else "ok"
        if said == "ok":
            block.update(is_error=False, content="done")
        elif said == "error":
            block.update(is_error=True, content="File does not exist.")
        else:
            block.update(is_error=True, content=f"Exit code {said}\nfailed")
        call["parent_tool_use_id"] = answer["parent_tool_use_id"] = by
        made += [call, answer]
    return text(events[:use_at] + made + events[use_at + 2:])


def judge(events: str, params: dict = READ_RULES, **run):
    return registry()["read_before_edit"].run(Evidence(**({"events": events, "exit_code": 0} | run)), params)


def outcome(events: str, params: dict = READ_RULES) -> tuple[str, str]:
    verdict = judge(events, params)
    return verdict.outcome, verdict.explanation


# --- What the check declares -----------------------------------------------------------------


def test_it_judges_the_agent_on_both_targets_from_the_stream_alone():
    spec = registry()["read_before_edit"]

    assert (spec.judges, spec.targets, spec.needs, spec.labels) == (
        "agent", ("kilo", "claude-code"), (), ("read_first", "edited_unread", "inconclusive"),
    )
    parsed = spec.parse_params(READ_RULES)
    assert (parsed.edits, parsed.whole_file) == (None, False)


@pytest.mark.parametrize(
    "params, location",
    [
        ({}, ("file",)),
        ({"file": ""}, ("file",)),
        ({"file": "("}, ("file",)),
        ({"file": "x*"}, ("file",)),
        (READ_RULES | {"edits": "x|"}, ("edits",)),
        (READ_RULES | {"edits": "["}, ("edits",)),
        (READ_RULES | {"colour": "red"}, ("colour",)),
        (READ_RULES | {"whole_file": "maybe"}, ("whole_file",)),
    ],
)
def test_a_missing_bad_or_unknown_parameter_is_refused(params, location):
    with pytest.raises(ValidationError) as raised:
        registry()["read_before_edit"].parse_params(params)

    assert [error["loc"] for error in raised.value.errors()] == [location]


def test_a_pattern_that_matches_every_path_is_refused_in_words():
    with pytest.raises(ValidationError, match="matches an empty path, so it would match every file"):
        registry()["read_before_edit"].parse_params({"file": "a|"})
    with pytest.raises(ValidationError, match="isn't a regular expression"):
        registry()["read_before_edit"].parse_params({"file": "[a-"})


# --- Kilo's read tool ------------------------------------------------------------------------


def test_a_read_then_an_edit_is_read_first_and_names_both_calls():
    verdict = judge(kilo(read(RULES), edit()))

    assert verdict.outcome == "pass"
    assert verdict.explanation == f"read of `{RULES}` (call 1) came before edit of `{CODE}` (call 2)"
    assert verdict.details == {"reads": [f"read of `{RULES}` (call 1)"], "edits": [f"edit of `{CODE}` (call 2)"]}
    assert registry()["read_before_edit"].label(verdict.outcome) == "read_first"


def test_an_edit_with_no_read_is_edited_unread():
    verdict = judge(kilo(read(CODE), edit()))

    assert (verdict.outcome, verdict.explanation) == ("fail", f"edit of `{CODE}` (call 2) ran with no read of a file matching file before it")
    assert registry()["read_before_edit"].label(verdict.outcome) == "edited_unread"


def test_a_read_after_the_edit_is_named_and_still_fails():
    assert outcome(kilo(edit(), read(RULES), edit())) == (
        "fail", f"edit of `{CODE}` (call 1) ran with no read of a file matching file before it; the read came after it: read of `{RULES}` (call 2)",
    )


def test_a_write_counts_as_an_edit():
    assert outcome(kilo(write(f"{PROJECT}/new.py"), read(RULES)))[0] == "fail"
    assert outcome(kilo(read(RULES), write()))[1] == f"read of `{RULES}` (call 1) came before write of `{CODE}` (call 2)"


def test_a_run_with_no_edits_is_inconclusive_not_a_pass():
    assert outcome(kilo(read(RULES))) == ("inconclusive", "no edit ran")
    assert outcome(kilo()) == ("inconclusive", "no edit ran")


def test_a_refused_edit_edited_nothing_and_a_refused_read_read_nothing():
    assert outcome(kilo(read(RULES), ("edit", edit()[1], "error"))) == ("inconclusive", "no edit ran; 1 edit call(s) didn't run")
    assert outcome(kilo(("read", {"filePath": RULES}, "error"), edit()))[0] == "fail"


def test_the_edits_pattern_picks_which_edits_are_judged():
    stream = kilo(edit(f"{PROJECT}/notes.md"), read(RULES), edit())

    assert outcome(stream)[0] == "fail"
    assert outcome(stream, READ_RULES | {"edits": r"\.py$"}) == (
        "pass", f"read of `{RULES}` (call 2) came before edit of `{CODE}` (call 3)",
    )
    assert outcome(kilo(edit(f"{PROJECT}/notes.md")), READ_RULES | {"edits": r"\.py$"}) == ("inconclusive", "no edit matching edits ran")


def test_a_partial_read_counts_unless_the_whole_file_is_asked_for():
    stream = kilo(read(RULES, offset=1, limit=20), edit())

    assert outcome(stream) == ("pass", f"read of `{RULES}` (call 1, partial) came before edit of `{CODE}` (call 2)")
    assert outcome(stream, READ_RULES | {"whole_file": True}) == (
        "fail", f"edit of `{CODE}` (call 2) ran with no whole read of a file matching file before it; partial: read of `{RULES}` (call 1, partial)",
    )
    assert outcome(kilo(read(RULES, offset=None, limit=None), edit()), READ_RULES | {"whole_file": True})[0] == "pass"


def test_the_latest_read_before_the_edit_is_named():
    assert outcome(kilo(read(RULES), read(RULES), edit()))[1].startswith(f"read of `{RULES}` (call 2)")


def test_only_the_first_edit_is_judged():
    assert outcome(kilo(read(RULES), edit(), edit()))[0] == "pass"
    assert outcome(kilo(edit(), read(RULES), edit()))[0] == "fail"


def test_a_grep_or_glob_is_not_a_read():
    stream = kilo(("grep", {"pattern": "x", "path": RULES}), ("glob", {"pattern": "CONSTRAINTS.md"}), edit())

    assert outcome(stream)[0] == "fail"


def test_a_read_or_edit_with_no_path_is_a_doubt():
    assert outcome(kilo(("read", {"filePath": 3}), edit())) == (
        "inconclusive", f"edit of `{CODE}` (call 2) ran with no read of a file matching file before it, but 1 read or edit call(s) have no file path",
    )
    assert outcome(kilo(read(RULES), ("edit", {"oldString": "a"})))[0] == "inconclusive"


def test_a_subagent_on_kilo_makes_any_order_inconclusive():
    with_subagent = kilo(edit(), read(RULES)) + "\n" + (DATA / "kilo-events" / "subagent-description.jsonl").read_text()

    assert outcome(with_subagent) == (
        "inconclusive",
        f"edit of `{CODE}` (call 1) ran with no read of a file matching file before it; the read came after it: read of `{RULES}` (call 2), "
        "but 1 subagent(s) ran, and a subagent's own tool calls aren't in the stream",
    )


def test_a_read_first_still_needs_a_finished_run():
    verdict = judge(kilo(read(RULES), edit()), exit_code=1)

    assert verdict.outcome == "inconclusive"
    assert verdict.explanation.endswith("but the run ended early, so the harness decided, not the agent")
    assert judge(kilo(edit()), exit_code=1).outcome == "fail"


def test_a_long_path_is_cut_in_the_explanation():
    long = f"{PROJECT}/{'d' * 100}/CONSTRAINTS.md"

    assert outcome(kilo(read(long), edit()))[1].startswith(f"read of `…{long[-80:]}` (call 1)")


# --- Reads and edits through the shell --------------------------------------------------------


@pytest.mark.parametrize(
    "command, partial",
    [
        ("cat CONSTRAINTS.md", False),
        (f"cat {RULES}", False),
        ("cd /tmp/x && cat 'CONSTRAINTS.md' README.md", False),
        ("less CONSTRAINTS.md", False),
        ("more CONSTRAINTS.md", False),
        ("/bin/cat CONSTRAINTS.md", False),
        ("LC_ALL=C cat CONSTRAINTS.md", False),
        ("cat < CONSTRAINTS.md", False),
        ("cat CONSTRAINTS.md | head -5", True),
        ("cat CONSTRAINTS.md | less", False),
        ("cat CONSTRAINTS.md 2>&1", False),
        ("cat CONSTRAINTS.md 2>/dev/null", False),
        ("ls; cat CONSTRAINTS.md", False),
        ("head -50 CONSTRAINTS.md", True),
        ("tail -n 5 CONSTRAINTS.md", True),
        ("sed -n '1,40p' CONSTRAINTS.md", True),
        ("sed -n -e '1,40p' CONSTRAINTS.md", True),
    ],
)
def test_a_shell_reader_counts_as_a_read(command, partial):
    verdict = judge(kilo(bash(command), edit()))

    assert verdict.outcome == "pass"
    assert verdict.explanation.startswith(f"`{command}` (call 1, exit 0{', partial' if partial else ''}) came before edit of")


@pytest.mark.parametrize(
    "command",
    [
        "grep -n rule CONSTRAINTS.md",
        "rg rule CONSTRAINTS.md",
        "ls CONSTRAINTS.md",
        "wc -l CONSTRAINTS.md",
        "echo CONSTRAINTS.md",
        "cat > CONSTRAINTS.md",
        "cat README.md > CONSTRAINTS.md",
        "cat <<CONSTRAINTS.md",
        "sed 's/a/b/' CONSTRAINTS.md",
        "sed -n '/CONSTRAINTS.md/p' README.md",
        "git show HEAD:CONSTRAINTS.md",
        "head",
    ],
)
def test_other_shell_commands_that_name_the_file_are_not_a_read(command):
    assert outcome(kilo(bash(command), edit()))[0] == "fail"
    assert not judge(kilo(bash(command), edit())).details["reads"]


def test_a_shell_read_that_failed_may_not_have_read_the_file():
    assert outcome(kilo(bash("cat CONSTRAINTS.md", 1), edit())) == (
        "inconclusive",
        f"edit of `{CODE}` (call 2) ran after `cat CONSTRAINTS.md` (call 1, exit 1), which exited non-zero, so it may not have read the file",
    )
    assert outcome(kilo(bash("cat CONSTRAINTS.md", 1), bash("cat CONSTRAINTS.md"), edit()))[0] == "pass"


def test_a_shell_command_that_may_write_before_the_read_keeps_a_pass_from_standing():
    verdict = judge(kilo(bash("sed -i 's/a/b/' till.py"), read(RULES), edit()))

    assert (verdict.outcome, verdict.explanation) == (
        "inconclusive",
        f"read of `{RULES}` (call 2) came before edit of `{CODE}` (call 3), "
        "but a shell command before the read may have written a file: `sed -i 's/a/b/' till.py` (call 1, exit 0)",
    )


@pytest.mark.parametrize(
    "command",
    ["echo x > till.py", "printf x >> till.py", "tee till.py", "cp a b", "mv a b", "rm a", "touch a", "perl -pi -e 's/a/b/' a",
     "sed -ni 's/a/b/' a", "sed --in-place 's/a/b/' a", "git checkout -- till.py", "git apply x.patch", "patch -p1 < x", "x &> log"],
)
def test_commands_taken_as_maybe_writing(command):
    assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "inconclusive"


@pytest.mark.parametrize(
    "command",
    ["ls -la", "python3 -m unittest 2>&1", "ls 2>/dev/null", "git status", "git diff > /dev/null", "find . -name '*.py'", "sed -n 1p a"],
)
def test_commands_that_do_not_write(command):
    assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "pass"


def test_a_shell_write_after_the_read_is_fine():
    assert outcome(kilo(read(RULES), bash("echo x > till.py"), edit()))[0] == "pass"


def test_a_run_whose_only_edits_were_through_the_shell_is_inconclusive_and_says_so():
    assert outcome(kilo(bash("echo x > till.py"))) == ("inconclusive", "no edit ran; 1 shell command(s) may have written a file")


def test_a_shell_call_with_no_command_is_a_doubt():
    assert outcome(kilo(("bash", {"command": ""}, 0), edit()))[1].endswith("but 1 shell call(s) have no command")


def test_a_command_that_will_not_split_is_split_on_spaces():
    assert outcome(kilo(bash("cat CONSTRAINTS.md 'unclosed"), edit()))[0] == "pass"


def test_a_heredoc_body_is_not_read_as_commands():
    assert outcome(kilo(bash("python3 - <<'EOF'\nprint(1)\nEOF\ncat CONSTRAINTS.md"), edit()))[0] == "pass"


# --- Claude Code -----------------------------------------------------------------------------


def test_claude_code_read_then_edit_is_read_first():
    stream = claude(("Read", {"file_path": RULES}), ("Edit", {"file_path": CODE, "old_string": "a", "new_string": "b"}))

    assert outcome(stream) == ("pass", f"Read of `{RULES}` (call 1) came before Edit of `{CODE}` (call 2)")


def test_claude_code_edit_with_no_read_and_its_other_edit_tools():
    for tool, key in (("Edit", "file_path"), ("Write", "file_path"), ("NotebookEdit", "notebook_path")):
        assert outcome(claude((tool, {key: CODE}), ("Read", {"file_path": RULES})))[0] == "fail"


def test_claude_code_a_read_that_errored_read_nothing():
    stream = claude(("Read", {"file_path": RULES}, "error"), ("Edit", {"file_path": CODE}))

    assert outcome(stream)[0] == "fail"


def test_claude_code_partial_read_and_shell_read():
    assert outcome(claude(("Read", {"file_path": RULES, "limit": 10}), ("Write", {"file_path": CODE})))[1].startswith(
        f"Read of `{RULES}` (call 1, partial)"
    )
    assert outcome(claude(("Bash", {"command": "cat CONSTRAINTS.md"}), ("Write", {"file_path": CODE})))[1].startswith(
        "`cat CONSTRAINTS.md` (call 1) came before"
    )
    assert outcome(claude(("Bash", {"command": "cat CONSTRAINTS.md"}, 1), ("Write", {"file_path": CODE})))[0] == "inconclusive"


def test_claude_code_a_subagents_reads_count_and_are_named():
    stream = claude(("Read", {"file_path": RULES}), ("Edit", {"file_path": CODE}), by="toolu_parent")

    assert outcome(stream) == ("pass", f"Read of `{RULES}` (call 1, by a subagent) came before Edit of `{CODE}` (call 2, by a subagent)")


def test_claude_code_kilo_tool_names_mean_nothing():
    assert outcome(claude(("read", {"file_path": RULES}), ("Edit", {"file_path": CODE})))[0] == "fail"


# --- Real streams -------------------------------------------------------------------------


def test_real_kilo_streams_read_then_edit_the_same_file():
    # tests-then-commit: read till.py, edit it. commit-without-tests: read the folder, then till.py, then edit it.
    for case in ("tests-then-commit", "commit-without-tests"):
        stream = (DATA / "kilo-events" / f"{case}.jsonl").read_text()
        assert outcome(stream, {"file": r"till\.py$"})[0] == "pass"
        assert outcome(stream, READ_RULES)[0] == "fail"


def test_real_claude_code_streams_read_then_edit():
    # skill-tool-denied: three Reads, the last CHANGELOG.md, then an Edit of CHANGELOG.md.
    stream = (DATA / "claude-code-events" / "skill-tool-denied.jsonl").read_text()

    assert outcome(stream, {"file": r"merged-changes\.md$"}) == (
        "pass", f"Read of `{PROJECT}/notes/merged-changes.md` (call 3) came before Edit of `{PROJECT}/CHANGELOG.md` (call 5)",
    )
    assert outcome(stream, READ_RULES)[0] == "fail"


def test_a_real_claude_code_shell_read_counts():
    # outside-bash-read: one Bash call, `cat ../outside/canary.txt`, and no edit.
    stream = (DATA / "claude-code-events" / "outside-bash-read.jsonl").read_text()

    assert outcome(stream, {"file": r"canary\.txt$"}) == ("inconclusive", "no edit ran")
    assert judge(stream, {"file": r"canary\.txt$"}).details["reads"] == ["`cat ../outside/canary.txt` (call 1)"]


# --- Real runs of the example (examples/read-before-edit, 2026-11-13) -------------------------


def test_the_example_run_without_the_rule_edited_with_no_read():
    # edit-without-read: the baseline read till.py and edited it, and never read CONSTRAINTS.md.
    stream = (DATA / "kilo-events" / "edit-without-read.jsonl").read_text()

    assert outcome(stream) == ("fail", f"edit of `{CODE}` (call 2) ran with no read of a file matching file before it")
    assert outcome(stream, {"file": r"till\.py$"}) == ("pass", f"read of `{CODE}` (call 1) came before edit of `{CODE}` (call 2)")


def test_the_example_run_that_read_through_the_shell():
    # shell-read-then-edit: `cat till.py`, an edit of till.py, then `cat till.py` again.
    stream = (DATA / "kilo-events" / "shell-read-then-edit.jsonl").read_text()

    assert outcome(stream, {"file": r"till\.py$"}) == ("pass", f"`cat till.py` (call 1, exit 0) came before edit of `{CODE}` (call 2)")
    assert outcome(stream)[0] == "fail"


def test_the_example_run_with_the_rule_read_constraints_first():
    # read-then-edit: the template read CONSTRAINTS.md, globbed, read till.py, then edited it.
    stream = (DATA / "kilo-events" / "read-then-edit.jsonl").read_text()

    assert outcome(stream) == ("pass", f"read of `{RULES}` (call 1) came before edit of `{CODE}` (call 4)")
    assert outcome(stream, READ_RULES | {"whole_file": True})[0] == "pass"


# --- Review fixes (2026-11-13) ---------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    ["cat CONSTRAINTS.md | grep rule", "cat CONSTRAINTS.md | wc -l", "cat CONSTRAINTS.md > /dev/null", "cat CONSTRAINTS.md >> notes.txt",
     "x=$(cat CONSTRAINTS.md)", "echo $(cat CONSTRAINTS.md | head)", "cat CONSTRAINTS.md 1>/dev/null"],
)
def test_a_reader_whose_output_the_agent_never_sees_is_not_a_read(command):
    verdict = judge(kilo(bash(command), edit()))

    assert verdict.outcome == "fail"
    assert verdict.details["reads"] == []


def test_a_reader_after_a_subshell_still_counts():
    assert outcome(kilo(bash("x=$(pwd); cat CONSTRAINTS.md"), edit()))[0] == "pass"


@pytest.mark.parametrize(
    "command",
    ["if [ -f CONSTRAINTS.md ]; then cat CONSTRAINTS.md; fi", "{ cat CONSTRAINTS.md; }", "for f in a; do cat CONSTRAINTS.md; done",
     "sudo cat CONSTRAINTS.md", "env LC_ALL=C cat CONSTRAINTS.md", "time cat CONSTRAINTS.md", "command cat CONSTRAINTS.md",
     "nohup cat CONSTRAINTS.md", "! cat CONSTRAINTS.md", "sudo -u me cat CONSTRAINTS.md", "while true; do less CONSTRAINTS.md; done"],
)
def test_a_reader_behind_a_keyword_or_a_wrapper_counts(command):
    assert outcome(kilo(bash(command), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["if true; then rm x; fi", "for f in a; do mv a b; done", "sudo tee x", "xargs rm"])
def test_a_writer_behind_a_keyword_or_a_wrapper_may_write(command):
    assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "inconclusive"


def test_a_continued_line_is_one_command():
    assert outcome(kilo(bash("cat \\\n  CONSTRAINTS.md"), edit()))[0] == "pass"


def test_a_quoted_string_across_lines_is_one_word():
    assert outcome(kilo(bash('echo "\ncat CONSTRAINTS.md\n"'), edit()))[0] == "fail"
    assert outcome(kilo(bash('git commit -m "fix\nrm stale helper"'), read(RULES), edit()))[0] == "pass"
    # An unclosed quote to the end is split on spaces, line by line.
    assert outcome(kilo(bash("echo 'x\ncat CONSTRAINTS.md"), edit()))[0] == "pass"


def test_a_heredoc_with_a_quote_in_its_body_is_skipped():
    assert outcome(kilo(bash("cat > notes <<EOF\ndon't\nEOF\ncat CONSTRAINTS.md"), read(RULES), edit()))[0] == "inconclusive"
    assert outcome(kilo(bash("python3 - <<EOF\ndon't\nEOF\ncat CONSTRAINTS.md"), edit()))[0] == "pass"


def kilo_steps(*steps) -> str:
    """A Kilo stream with several calls in a step: each step a list of calls, as kilo() takes them."""
    events = []
    for calls in steps:
        built = [json.loads(line) for line in kilo(*calls).splitlines()]
        uses = [event for event in built if event["type"] == "tool_use"]
        events += [built[0], built[1], *uses, built[3]]
    last = [json.loads(line) for line in kilo().splitlines()]
    return text(events + last)


def test_a_read_in_the_same_step_as_the_edit_is_edited_unread():
    # Decided by Christopher Chant, 2026-11-13: the agent wrote the edit before it saw the file, so it fails.
    verdict = judge(kilo_steps([read(RULES), edit()]))

    assert (verdict.outcome, verdict.explanation) == (
        "fail",
        f"edit of `{CODE}` (call 2) ran with no read of a file matching file before it; "
        f"read of `{RULES}` (call 1) came in the same step as the edit, so the edit was written before the read's result came back",
    )
    assert registry()["read_before_edit"].label(verdict.outcome) == "edited_unread"


def test_a_read_in_an_earlier_step_still_counts_beside_one_in_the_edits_step():
    assert outcome(kilo_steps([read(RULES)], [read(RULES), edit()])) == (
        "pass", f"read of `{RULES}` (call 1) came before edit of `{CODE}` (call 3)",
    )


def test_claude_code_a_read_in_the_same_turn_as_the_edit_is_edited_unread():
    events = [json.loads(line) for line in claude(("Read", {"file_path": RULES}), ("Edit", {"file_path": CODE})).splitlines()]
    uses = [event for event in events if event["type"] == "assistant" and event["message"]["content"][0]["type"] == "tool_use"]
    uses[1]["message"]["id"] = uses[0]["message"]["id"]

    assert outcome(text(events)) == (
        "fail",
        f"Edit of `{CODE}` (call 2) ran with no read of a file matching file before it; "
        f"Read of `{RULES}` (call 1) came in the same step as the edit, so the edit was written before the read's result came back",
    )
    uses[1]["message"]["id"] = uses[0]["message"]["id"] + "x"
    assert outcome(text(events))[0] == "pass"


def test_a_write_before_the_read_in_the_same_command_keeps_a_pass_from_standing():
    assert outcome(kilo(bash("sed -i 's/a/b/' till.py; cat CONSTRAINTS.md"), edit())) == (
        "inconclusive",
        f"`sed -i 's/a/b/' till.py; cat CONSTRAINTS.md` (call 1, exit 0) came before edit of `{CODE}` (call 2), "
        "but a shell command before the read may have written a file: `sed -i 's/a/b/' till.py; cat CONSTRAINTS.md` (call 1, exit 0)",
    )
    assert outcome(kilo(bash("cat CONSTRAINTS.md && echo x > till.py"), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["cat CONSTRAINTS.md || true", "cat CONSTRAINTS.md; echo done", "cat CONSTRAINTS.md & wait"])
def test_a_shell_read_whose_exit_may_be_another_commands_is_unsure(command):
    assert outcome(kilo(bash(command), edit())) == (
        "inconclusive",
        f"edit of `{CODE}` (call 2) ran after `{command}` (call 1, exit 0), whose exit may be another command's, so it may not have read the file",
    )


@pytest.mark.parametrize("command", ["cat CONSTRAINTS.md && echo done", "cd x && cat CONSTRAINTS.md", "ls; cat CONSTRAINTS.md"])
def test_a_shell_read_whose_exit_is_its_own_counts(command):
    assert outcome(kilo(bash(command), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["git clean -fd", "git revert HEAD", "echo x >&notes.txt", "awk -i inplace '{print}' a"])
def test_more_commands_taken_as_maybe_writing(command):
    assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "inconclusive"


def test_a_redirect_to_a_file_descriptor_or_from_one_writes_nothing():
    assert outcome(kilo(bash("ls >&2"), read(RULES), edit()))[0] == "pass"
    assert outcome(kilo(bash("ls 2>&-"), read(RULES), edit()))[0] == "pass"


def test_an_option_value_or_a_file_descriptor_is_not_a_path():
    assert judge(kilo(bash("head -n 5 CONSTRAINTS.md"), edit()), {"file": "^5$"}).details["reads"] == []
    assert judge(kilo(bash("cat CONSTRAINTS.md 2>&1"), edit()), {"file": "^2$"}).details["reads"] == []
    assert judge(kilo(bash("sed -n --expression 1p CONSTRAINTS.md"), edit()), {"file": "1p"}).details["reads"] == []
    assert outcome(kilo(bash("sed -n --expression=1p CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("sed --quiet -f script.sed CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("sed --silent '1p' CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("sed -ne '1p' CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("sed -n '1p' CONSTRAINTS.md"), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["sed '1p' CONSTRAINTS.md", "sed -ni '1p' CONSTRAINTS.md", "sed -n --in-place '1p' CONSTRAINTS.md",
                                     "sed -n -i.bak '1p' CONSTRAINTS.md", "sed -E '1p' CONSTRAINTS.md"])
def test_sed_that_prints_everything_or_edits_in_place_is_not_a_read(command):
    assert judge(kilo(bash(command), edit())).details["reads"] == []


def test_git_options_before_the_subcommand_are_skipped():
    for command in ("git -C /tmp/x checkout -- a", "git -c a=b checkout a", "git --no-pager stash"):
        assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "inconclusive", command
    for command in ("git -C checkout status", "git -c checkout log", "git"):
        assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "pass", command


def test_a_tool_that_runs_shell_commands_unseen_is_a_doubt():
    assert outcome(kilo(read(RULES), ("background_process", {"command": "x"}), edit()))[1].endswith(
        "but 1 call(s) to a tool that runs shell commands Whitney doesn't read: background_process"
    )
    assert outcome(claude(("Read", {"file_path": RULES}), ("Monitor", {"command": "x"}), ("Edit", {"file_path": CODE})))[0] == "inconclusive"


def test_an_edit_with_no_path_is_a_doubt_whatever_its_edits_pattern():
    assert outcome(kilo(read(RULES), ("write", {"content": "x"}), edit()), READ_RULES | {"edits": r"\.md$"})[1].endswith(
        "but 1 read or edit call(s) have no file path"
    )


def test_details_list_unsure_reads_and_every_judged_edit():
    verdict = judge(kilo(bash("cat CONSTRAINTS.md", 1), read(RULES), edit(), write()))

    assert verdict.details == {
        "reads": [f"`cat CONSTRAINTS.md` (call 1, exit 1)", f"read of `{RULES}` (call 2)"],
        "edits": [f"edit of `{CODE}` (call 3)", f"write of `{CODE}` (call 4)"],
    }


def test_a_doubt_keeps_the_details():
    verdict = judge(kilo(read(RULES), ("read", {"filePath": ""}), edit()))

    assert verdict.outcome == "inconclusive"
    assert verdict.details == {"reads": [f"read of `{RULES}` (call 1)"], "edits": [f"edit of `{CODE}` (call 3)"]}
    assert judge(kilo(("bash", {"command": ""}, 0), read(RULES), edit())).details["edits"] == [f"edit of `{CODE}` (call 3)"]


def test_several_counts_in_one_run():
    stream = kilo(("bash", {"command": ""}, 0), ("bash", {"command": ""}, 0), edit())
    assert outcome(stream)[1].endswith("but 2 shell call(s) have no command")
    stream = kilo(("read", {}), ("read", {}), edit())
    assert outcome(stream)[1].endswith("but 2 read or edit call(s) have no file path")


def test_whole_file_ignores_a_failed_partial_shell_read():
    assert outcome(kilo(bash("head CONSTRAINTS.md", 1), edit()), READ_RULES | {"whole_file": True}) == (
        "fail", f"edit of `{CODE}` (call 2) ran with no whole read of a file matching file before it; partial: `head CONSTRAINTS.md` (call 1, exit 1, partial)",
    )


def test_a_partial_read_after_the_edit_is_not_named_as_before_it():
    assert outcome(kilo(edit(), read(RULES, limit=5)), READ_RULES | {"whole_file": True}) == (
        "fail", f"edit of `{CODE}` (call 1) ran with no read of a file matching file before it",
    )


def test_a_failed_shell_read_after_the_edit_is_not_named():
    assert outcome(kilo(edit(), bash("cat CONSTRAINTS.md", 1))) == (
        "fail", f"edit of `{CODE}` (call 1) ran with no read of a file matching file before it",
    )


def test_a_partial_read_tool_call_needs_an_offset_or_a_limit():
    assert outcome(kilo(read(RULES, offset=3), edit()))[1].startswith(f"read of `{RULES}` (call 1, partial)")
    assert outcome(kilo(read(RULES, limit=3), edit()))[1].startswith(f"read of `{RULES}` (call 1, partial)")


def test_a_path_of_exactly_the_shown_length_is_not_cut():
    exact = "/" + "p" * 64 + "/CONSTRAINTS.md"
    assert len(exact) == 80
    assert outcome(kilo(read(exact), edit()))[1].startswith(f"read of `{exact}` (call 1)")


def test_a_long_command_is_cut_from_its_start():
    long = "cat CONSTRAINTS.md " + "x" * 100
    assert outcome(kilo(bash(long), edit()))[1].startswith(f"`{long[:80]}…` (call 1, exit 0)")



# --- Shell commands, edge by edge (mutation testing) ------------------------------------------


def reads_of(command: str, pattern: str) -> list[str]:
    """The reads the check records in a run of one shell command and an edit, with `file` set to `pattern`."""
    return judge(kilo(bash(command), edit()), {"file": pattern}).details["reads"]


def test_a_new_line_separates_commands_and_masks_the_exit():
    assert outcome(kilo(bash("ls\ncat CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("cat CONSTRAINTS.md\necho done"), edit()))[0] == "inconclusive"
    assert outcome(kilo(bash("cat CONSTRAINTS.md\necho 'x"), edit()))[0] == "inconclusive"
    assert outcome(kilo(bash("cat CONSTRAINTS.md\nls 'x"), edit()), READ_RULES)[1].startswith("edit of")


def test_an_escaped_backslash_at_the_end_of_a_line_does_not_continue_it():
    assert outcome(kilo(bash("echo a\\\\\ncat CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("echo a\\\\\\\ncat CONSTRAINTS.md"), edit()))[0] == "fail"
    assert outcome(kilo(bash("cat \\\nCONSTRAINTS.md"), edit()))[0] == "pass"


def test_a_heredocs_body_is_not_run():
    assert outcome(kilo(bash("python3 - <<EOF\nx\ncat CONSTRAINTS.md\nEOF"), edit()))[0] == "fail"
    assert outcome(kilo(bash("python3 - <<-EOF\n\tx\n\tEOF\ncat CONSTRAINTS.md"), edit()))[0] == "pass"


def test_a_word_that_is_not_one_of_shlexs_word_characters_stays_in_the_path():
    assert outcome(kilo(bash("cat notes/v1+2,draft.md"), edit()), {"file": r"v1\+2,draft\.md$"})[0] == "pass"


def test_a_trailing_option_with_no_value_is_left_alone():
    assert outcome(kilo(bash("head CONSTRAINTS.md -n"), edit()))[0] == "pass"
    assert outcome(kilo(bash("sed -n 1p CONSTRAINTS.md -e"), edit()))[0] == "pass"
    assert outcome(kilo(bash("git -C"), read(RULES), edit()))[0] == "pass"


def test_options_are_not_paths_but_cats_n_takes_no_value():
    assert reads_of("cat -n CONSTRAINTS.md", "^-") == []
    assert outcome(kilo(bash("cat -n CONSTRAINTS.md"), edit()))[0] == "pass"
    assert reads_of("tail -n 5 CONSTRAINTS.md", "^5$") == []
    assert reads_of("tail -c 5 CONSTRAINTS.md", "^5$") == []
    assert reads_of("sed --quiet 1p CONSTRAINTS.md", "^1p$") == []
    assert reads_of("sed -n -s 1p CONSTRAINTS.md", "^-") == []


def test_every_sed_script_is_skipped_whatever_gives_it():
    assert reads_of("sed -n -e 1p -e 2p CONSTRAINTS.md", "^2p$") == []
    assert reads_of("sed -ne 1p -ne 2p CONSTRAINTS.md", "^2p$") == []
    assert reads_of("sed -n -f a.sed -f b.sed CONSTRAINTS.md", r"^b\.sed$") == []
    assert reads_of("sed -n --expression 1p --expression 2p CONSTRAINTS.md", "^2p$") == []
    assert reads_of("sed -n --file a.sed --file b.sed CONSTRAINTS.md", r"^b\.sed$") == []
    assert outcome(kilo(bash("sed -n --file=a.sed CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("sed -n --expression=1p CONSTRAINTS.md"), edit()))[0] == "pass"
    assert reads_of("sed -n --posix 1p CONSTRAINTS.md", "^--") == []


@pytest.mark.parametrize("command", ["cat CONSTRAINTS.md |& grep rule", "cat CONSTRAINTS.md | grep rule; less x", "cat CONSTRAINTS.md &> /dev/null",
                                     "cat CONSTRAINTS.md &>/dev/null", "cat CONSTRAINTS.md >&notes.txt", "x=$(echo $(cat CONSTRAINTS.md))"])
def test_more_readers_whose_output_the_agent_never_sees(command):
    assert reads_of(command, r"CONSTRAINTS\.md$") == []


def test_a_trailing_pipe_or_redirect_is_left_alone():
    assert outcome(kilo(bash("cat CONSTRAINTS.md |"), edit()))[0] == "pass"
    assert outcome(kilo(bash("cat CONSTRAINTS.md <"), edit()))[0] == "pass"
    assert outcome(kilo(bash("ls >"), read(RULES), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["ls &>/dev/null", "ls >/dev/null", "awk '{print}' inplace", "sed -n 1p till.py", "perl script.pl till.py",
                                     "sed -n 1p --in", "cat x <&3", "cat <<< 'rm x'"])
def test_still_more_commands_that_do_not_write(command):
    assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["gawk -i inplace '{print}' a", "sudo -E rm a", "env -u HOME rm a", "ls 2> err.log"])
def test_still_more_commands_taken_as_maybe_writing(command):
    assert outcome(kilo(bash(command), read(RULES), edit()))[0] == "inconclusive"


def test_a_wrappers_option_without_a_value_is_skipped_alone():
    assert outcome(kilo(bash("sudo -E cat CONSTRAINTS.md"), edit()))[0] == "pass"
    assert outcome(kilo(bash("xargs -0 cat CONSTRAINTS.md"), edit()))[0] == "pass"


def test_a_command_that_reads_and_writes_at_once_wrote_no_earlier_than_it_read():
    assert outcome(kilo(bash("cat CONSTRAINTS.md 2> err.log"), edit()))[0] == "pass"


def test_a_shell_read_in_the_same_step_as_the_edit_is_edited_unread():
    assert outcome(kilo_steps([bash("cat CONSTRAINTS.md"), edit()]))[0] == "fail"


def test_a_same_step_read_still_fails_under_whole_file_and_a_doubt_keeps_it_inconclusive():
    assert outcome(kilo_steps([read(RULES, limit=5), edit()]), READ_RULES | {"whole_file": True})[1].endswith("partial: read of "
        f"`{RULES}` (call 1, partial)")
    with_subagent = kilo_steps([read(RULES), edit()]) + "\n" + (DATA / "kilo-events" / "subagent-description.jsonl").read_text()
    assert outcome(with_subagent)[0] == "inconclusive"


def test_the_real_template_run_with_its_read_moved_into_the_edits_step_fails():
    # read-then-edit (Kilo 7.8.27): read CONSTRAINTS.md, glob, read till.py, edit, each in its own step. Dropping
    # the step boundaries between the first read and the edit puts all four calls in one step.
    events = [json.loads(line) for line in (DATA / "kilo-events" / "read-then-edit.jsonl").read_text().splitlines()]
    uses = [i for i, event in enumerate(events) if event["type"] == "tool_use"]
    first, edit_at = uses[0], uses[3]
    merged = events[:first] + [
        event for event in events[first:edit_at] if event["type"] not in ("step_start", "step_finish")
    ] + events[edit_at:]

    assert outcome(text(merged)) == (
        "fail",
        f"edit of `{CODE}` (call 4) ran with no read of a file matching file before it; "
        f"read of `{RULES}` (call 1) came in the same step as the edit, so the edit was written before the read's result came back",
    )
    assert outcome(text(events))[0] == "pass"


def test_each_refused_edit_is_counted():
    stream = kilo(("edit", edit()[1], "error"), ("write", write()[1], "error"))
    assert outcome(stream) == ("inconclusive", "no edit ran; 2 edit call(s) didn't run")


def test_another_tool_that_ran_is_not_a_doubt():
    assert outcome(kilo(read(RULES), ("glob", {"pattern": "*"}), edit()))[0] == "pass"
    assert outcome(kilo(read(RULES), ("background_process", {"command": "x"}, "error"), edit()))[0] == "pass"


def test_an_edit_the_edits_pattern_skips_leaves_the_later_ones_judged():
    stream = kilo(edit(f"{PROJECT}/notes.md"), edit(), read(RULES), edit(f"{PROJECT}/b.py"))
    assert outcome(stream, READ_RULES | {"edits": r"\.py$"}) == (
        "fail", f"edit of `{CODE}` (call 2) ran with no read of a file matching file before it; the read came after it: read of `{RULES}` (call 3)",
    )


def test_a_read_that_did_not_run_of_another_file_is_not_a_read():
    assert outcome(kilo(("read", {"filePath": CODE}, "error"), edit()))[0] == "fail"
    assert judge(kilo(read(CODE), edit())).details["reads"] == []


def test_a_command_of_exactly_the_shown_length_is_not_cut():
    exact = "cat CONSTRAINTS.md " + "x" * (80 - len("cat CONSTRAINTS.md "))
    assert outcome(kilo(bash(exact), edit()))[1].startswith(f"`{exact}` (call 1, exit 0)")


def test_shell_files_names_each_reader_and_writer_by_its_place():
    from whitney.checks.reads import Reading, Shell, shell_files

    assert shell_files("ls; cat a | head; rm b") == Shell(
        (Reading("a", False, 1, True),), (3,),
    )
    assert shell_files("") == Shell((), ())


def test_each_edit_with_no_path_is_counted():
    stream = kilo(read(RULES), ("edit", {"oldString": "a"}), ("write", {"content": "x"}))
    assert outcome(stream)[1].endswith("but 2 read or edit call(s) have no file path")


def test_a_write_after_the_read_in_the_same_command_is_after_it():
    assert outcome(kilo(bash("ls && cat CONSTRAINTS.md && echo x > till.py"), edit()))[0] == "pass"


@pytest.mark.parametrize("command", ["(cat CONSTRAINTS.md)", "ls; (cat CONSTRAINTS.md)", "(cat CONSTRAINTS.md); echo $",
                                     "x=$(pwd);(cat CONSTRAINTS.md)", "x=$(echo $(pwd)); cat CONSTRAINTS.md"])
def test_a_subshell_is_not_a_command_substitution(command):
    assert reads_of(command, r"CONSTRAINTS\.md$") != []


@pytest.mark.parametrize("command", ["x=$(echo $(pwd) && cat CONSTRAINTS.md)", "x=$(pwd | cat CONSTRAINTS.md)",
                                     "x=$(pwd); y=$(cat CONSTRAINTS.md)"])
def test_a_reader_anywhere_inside_a_command_substitution_is_hidden(command):
    assert reads_of(command, r"CONSTRAINTS\.md$") == []


def test_a_reader_reads_its_files_after_an_input_redirect_too():
    assert reads_of("cat < CONSTRAINTS.md README.md", r"README\.md$") == ["`cat < CONSTRAINTS.md README.md` (call 1, exit 0)"]


def test_a_long_option_after_seds_script_takes_no_value():
    assert outcome(kilo(bash("sed -n 1p --separate CONSTRAINTS.md"), edit()))[0] == "pass"


def test_an_earlier_read_that_may_have_failed_keeps_a_same_step_read_from_failing():
    stream = kilo_steps([bash("cat CONSTRAINTS.md", 1)], [read(RULES), edit()])

    assert outcome(stream) == (
        "inconclusive",
        f"edit of `{CODE}` (call 3) ran after `cat CONSTRAINTS.md` (call 1, exit 1), which exited non-zero, so it may not have read the file",
    )
    # A read that may have failed in the edit's own step is no doubt: its result came back too late either way.
    assert outcome(kilo_steps([bash("cat CONSTRAINTS.md", 1), read(RULES), edit()]))[0] == "fail"
