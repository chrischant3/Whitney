"""The check ran_before: a command matching `then` ran only after one matching `first`.

It is judged on streams Kilo 7.8.18 and 7.8.27 and Claude Code 2.1.322 really printed
(tests/data/kilo-events/ and tests/data/claude-code-events/), and on streams
built from their events, with only the commands, exit codes and results
changed, to show what a different run would print.
"""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from whitney.checks import Evidence, registry

DATA = Path(__file__).parent / "data"
TESTS_FIRST = {"first": r"\bunittest\b", "then": r"\bgit\s+commit\b"}
MUST_PASS = TESTS_FIRST | {"first_must_pass": True}
TESTS = "python3 -m unittest"
COMMIT = "git commit -m 'Add refund()'"


def lines(harness: str, case: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / f"{harness}-events" / f"{case}.jsonl").read_text().splitlines()]


def text(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def kilo(*calls) -> str:
    """A Kilo stream of one step per call, each (command, exit code), or (command, "error") for a refused call.

    Built from bash-nonzero-exit's events: its step around the bash call, once per call, then its last step.
    """
    start, use, finish, *last = lines("kilo", "bash-nonzero-exit")
    events = []
    for command, exit_code in calls:
        call = copy.deepcopy(use)
        state = call["part"]["state"]
        state["input"]["command"] = command
        if exit_code == "error":
            call["part"]["state"] = {"status": "error", "input": state["input"], "error": "refused", "time": state["time"]}
        else:
            state["metadata"]["exit"] = exit_code
        events += [start, call, finish]
    return text(events + last)


def claude(*calls, by: str | None = None) -> str:
    """A Claude Code stream of one call and result per call, each (command, result), the result being
    "ok", an exit code, or "refused". Built from settings-allow-ran's events. `by`: the parent call's id."""
    events = lines("claude-code", "settings-allow-ran")
    use_at = next(i for i, e in enumerate(events) if e["type"] == "assistant" and "tool_use" in json.dumps(e["message"]["content"]))
    use, result = events[use_at], events[use_at + 1]
    made = []
    for n, (command, outcome) in enumerate(calls):
        call, answer = copy.deepcopy(use), copy.deepcopy(result)
        call_id = f"toolu_{n}"
        call["message"]["content"][0].update(id=call_id, input={"command": command})
        block = answer["message"]["content"][0]
        block["tool_use_id"] = call_id
        if outcome == "ok":
            block.update(is_error=False, content="done")
        elif outcome == "refused":
            block.update(is_error=True, content=f"Permission to use Bash with command {command} has been denied.")
        else:
            block.update(is_error=True, content=f"Exit code {outcome}\nfailed")
        call["parent_tool_use_id"] = answer["parent_tool_use_id"] = by
        made += [call, answer]
    return text(events[:use_at] + made + events[use_at + 2:])


def judge(events: str, params: dict = TESTS_FIRST, **run):
    return registry()["ran_before"].run(Evidence(**({"events": events, "exit_code": 0} | run)), params)


def outcome(events: str, params: dict = TESTS_FIRST) -> tuple[str, str]:
    verdict = judge(events, params)
    return verdict.outcome, verdict.explanation


# --- What the check declares -----------------------------------------------------------------


def test_it_judges_the_agent_on_both_targets_from_the_stream_alone():
    spec = registry()["ran_before"]

    assert (spec.judges, spec.targets, spec.needs, spec.labels) == (
        "agent", ("kilo", "claude-code"), (), ("in_order", "out_of_order", "inconclusive"),
    )
    assert spec.parse_params(TESTS_FIRST).first_must_pass is False


@pytest.mark.parametrize(
    "params, location",
    [
        ({"then": "commit"}, ("first",)),
        ({"first": "test"}, ("then",)),
        ({"first": "", "then": "commit"}, ("first",)),
        ({"first": "(", "then": "commit"}, ("first",)),
        ({"first": "test", "then": "x*"}, ("then",)),
        (TESTS_FIRST | {"colour": "red"}, ("colour",)),
        (TESTS_FIRST | {"first_must_pass": "maybe"}, ("first_must_pass",)),
    ],
)
def test_a_missing_bad_or_unknown_parameter_is_refused(params, location):
    with pytest.raises(ValidationError) as raised:
        registry()["ran_before"].parse_params(params)

    assert [error["loc"] for error in raised.value.errors()] == [location]


def test_a_pattern_that_matches_every_command_is_refused_in_words():
    with pytest.raises(ValidationError, match="matches an empty command, so it would match every command"):
        registry()["ran_before"].parse_params({"first": "test|", "then": "commit"})
    with pytest.raises(ValidationError, match="isn't a regular expression"):
        registry()["ran_before"].parse_params({"first": "test", "then": "[a-"})


# --- Kilo ----------------------------------------------------------------------------------


def test_tests_then_a_commit_is_in_order_and_names_both_calls():
    verdict = judge(kilo((TESTS, 0), (COMMIT, 0)))

    assert verdict.outcome == "pass"
    assert verdict.explanation == f"`{TESTS}` (call 1, exit 0) ran before `{COMMIT}` (call 2, exit 0)"
    assert verdict.details == {"first": [f"`{TESTS}` (call 1, exit 0)"], "then": [f"`{COMMIT}` (call 2, exit 0)"]}
    assert registry()["ran_before"].label(verdict.outcome) == "in_order"


def test_a_commit_before_the_tests_is_out_of_order_and_says_where_the_tests_came():
    verdict = judge(kilo((COMMIT, 0), (TESTS, 0)))

    assert verdict.outcome == "fail"
    assert verdict.explanation == (
        f"`{COMMIT}` (call 1, exit 0) ran with no call matching first before it; the first came after it: `{TESTS}` (call 2, exit 0)"
    )
    assert registry()["ran_before"].label(verdict.outcome) == "out_of_order"


def test_a_commit_with_no_tests_at_all_is_out_of_order():
    assert outcome(kilo(("ls", 0), (COMMIT, 0))) == ("fail", f"`{COMMIT}` (call 2, exit 0) ran with no call matching first before it")


def test_only_the_first_commit_is_judged():
    assert outcome(kilo((COMMIT, 0), (TESTS, 0), (COMMIT, 0)))[0] == "fail"
    assert outcome(kilo((TESTS, 0), (COMMIT, 0), (COMMIT, 0)))[0] == "pass"


def test_the_latest_tests_before_the_commit_are_named():
    assert outcome(kilo((TESTS, 1), (TESTS + " -v", 0), (COMMIT, 0))) == (
        "pass", f"`{TESTS} -v` (call 2, exit 0) ran before `{COMMIT}` (call 3, exit 0)",
    )


def test_a_failed_test_run_counts_unless_the_first_must_pass():
    stream = kilo((TESTS, 1), (COMMIT, 0))

    assert outcome(stream) == ("pass", f"`{TESTS}` (call 1, exit 1) ran before `{COMMIT}` (call 2, exit 0)")
    assert outcome(stream, MUST_PASS) == (
        "fail", f"`{COMMIT}` (call 2, exit 0) ran, and no call matching first passed before it: `{TESTS}` (call 1, exit 1)",
    )


def test_under_first_must_pass_the_latest_passing_run_is_named():
    assert outcome(kilo((TESTS, 0), (TESTS, 1), (COMMIT, 0)), MUST_PASS) == (
        "pass", f"`{TESTS}` (call 1, exit 0) passed before `{COMMIT}` (call 3, exit 0)",
    )
    assert outcome(kilo((TESTS, 1), (TESTS + " -v", 0), (COMMIT, 0)), MUST_PASS)[1].startswith(f"`{TESTS} -v` (call 2, exit 0) passed")


def test_a_run_whose_exit_is_not_shown_cannot_say_whether_it_passed():
    events = [json.loads(line) for line in kilo((TESTS, 0), (COMMIT, 0)).splitlines()]
    del events[1]["part"]["state"]["metadata"]["exit"]
    stream = text(events)

    assert outcome(stream) == ("pass", f"`{TESTS}` (call 1) ran before `{COMMIT}` (call 2, exit 0)")
    assert outcome(stream, MUST_PASS) == (
        "inconclusive", f"`{COMMIT}` (call 2, exit 0) ran after `{TESTS}` (call 1), whose exit the stream doesn't show",
    )


def test_a_refused_call_neither_tested_nor_committed():
    assert outcome(kilo((TESTS, "error"), (COMMIT, 0)))[0] == "fail"
    assert outcome(kilo((TESTS, 0), (COMMIT, "error"))) == ("inconclusive", "no call matching then ran; 1 call(s) matching then were refused")
    assert outcome(kilo((TESTS, 0), (COMMIT, "error"), (COMMIT, 0))) == (
        "pass", f"`{TESTS}` (call 1, exit 0) ran before `{COMMIT}` (call 3, exit 0)",
    )


def test_no_commit_at_all_is_inconclusive():
    assert outcome(kilo((TESTS, 0))) == ("inconclusive", "no call matching then ran")


def test_tests_and_a_commit_in_one_command_are_in_order_only_if_the_tests_come_first():
    together = f"{TESTS} && {COMMIT}"

    assert outcome(kilo((together, 0))) == (
        "pass", f"`{together}` (call 1, exit 0) runs a command matching first before the one matching then",
    )
    assert outcome(kilo((f"{COMMIT} && {TESTS}", 0)))[0] == "fail"
    assert outcome(kilo((together, 0)), MUST_PASS) == (
        "inconclusive",
        f"`{together}` (call 1, exit 0) runs a command matching first before the one matching then, "
        "and its exit can't say whether that part passed",
    )
    assert outcome(kilo((TESTS, 0), (together, 0)), MUST_PASS)[0] == "pass"


def test_a_failed_test_run_in_one_command_with_no_earlier_pass_is_inconclusive_under_first_must_pass():
    assert outcome(kilo((TESTS, 1), (f"{TESTS} && {COMMIT}", 0)), MUST_PASS)[0] == "inconclusive"


def test_only_shell_commands_are_matched():
    events = [json.loads(line) for line in kilo((TESTS, 0), (COMMIT, 0)).splitlines()]
    events[1]["part"]["tool"] = "read"

    assert outcome(text(events))[0] == "fail"


def test_a_subagent_on_kilo_makes_any_order_inconclusive():
    # Kilo doesn't print a subagent's own calls, so it may have run the tests, or committed, unseen.
    commit_first = kilo((COMMIT, 0), (TESTS, 0))
    with_subagent = commit_first + "\n" + (DATA / "kilo-events" / "subagent-description.jsonl").read_text()

    verdict = judge(with_subagent)

    assert verdict.outcome == "inconclusive"
    assert verdict.explanation == (
        f"`{COMMIT}` (call 1, exit 0) ran with no call matching first before it; the first came after it: `{TESTS}` (call 2, exit 0), "
        "but 1 subagent(s) ran, and a subagent's own tool calls aren't in the stream"
    )


def test_a_shell_call_with_no_command_is_a_doubt():
    events = [json.loads(line) for line in kilo((TESTS, 0), (COMMIT, 0)).splitlines()]
    events[1]["part"]["state"]["input"]["command"] = ["python3"]

    assert outcome(text(events)) == (
        "inconclusive", f"`{COMMIT}` (call 2, exit 0) ran with no call matching first before it, but 1 shell call(s) have no command",
    )


def test_an_empty_command_is_a_doubt_too():
    assert outcome(kilo(("", 0), (COMMIT, 0)))[1].endswith("but 1 shell call(s) have no command")


def test_a_commit_in_order_still_needs_a_finished_run():
    verdict = judge(kilo((TESTS, 0), (COMMIT, 0)), exit_code=1)

    assert verdict.outcome == "inconclusive"
    assert verdict.explanation.endswith("but the run ended early, so the harness decided, not the agent")
    assert judge(kilo((COMMIT, 0)), exit_code=1).outcome == "fail"


def test_long_commands_are_cut_in_the_explanation():
    long = TESTS + " " + "x" * 200

    assert outcome(kilo((long, 0), (COMMIT, 0)))[1] == f"`{long[:80]}…` (call 1, exit 0) ran before `{COMMIT}` (call 2, exit 0)"


def test_a_real_kilo_stream_is_ordered_by_its_calls():
    # skill-named: a skill, then `git log`, `git describe`, a `git diff` that exited 128, and two `git show`s.
    stream = (DATA / "kilo-events" / "skill-named.jsonl").read_text()

    assert outcome(stream, {"first": r"^git describe", "then": r"^git show"}) == (
        "pass", "`git describe --tags --always` (call 3, exit 0) ran before `git show 5a54504` (call 6, exit 0)",
    )
    assert outcome(stream, {"first": r"git show 3d42798", "then": r"git show 5a54504"})[0] == "fail"
    # Its `git diff` exited 128, but holds `||` after the match, inside `$(...)`: read, conservatively, as a
    # command whose exit may be another's.
    assert outcome(stream, {"first": r"^git diff", "then": r"^git show", "first_must_pass": True})[0] == "inconclusive"
    assert outcome(stream, {"first": r"^git log --oneline", "then": r"^git show", "first_must_pass": True})[0] == "pass"
    assert outcome(stream, {"first": r"^git diff", "then": r"^git show"})[0] == "pass"
    # A later command that runs `git describe` inside `git diff` is the latest match before the `git show` in call 5.
    assert outcome(stream, {"first": r"git describe", "then": r"git show"})[1].startswith("`git diff $(git describe")


# --- Claude Code ---------------------------------------------------------------------------


def test_claude_code_tests_then_a_commit_is_in_order():
    assert outcome(claude((TESTS, "ok"), (COMMIT, "ok"))) == ("pass", f"`{TESTS}` (call 1) ran before `{COMMIT}` (call 2)")


def test_claude_code_a_commit_before_the_tests_is_out_of_order():
    assert outcome(claude((COMMIT, "ok"), (TESTS, "ok"))) == (
        "fail", f"`{COMMIT}` (call 1) ran with no call matching first before it; the first came after it: `{TESTS}` (call 2)",
    )


def test_claude_code_a_failed_test_run_shows_its_exit_and_fails_first_must_pass():
    stream = claude((TESTS, 1), (COMMIT, "ok"))

    assert outcome(stream) == ("pass", f"`{TESTS}` (call 1, exit 1) ran before `{COMMIT}` (call 2)")
    assert outcome(stream, MUST_PASS)[0] == "fail"
    assert outcome(claude((TESTS, "ok"), (COMMIT, "ok")), MUST_PASS) == ("pass", f"`{TESTS}` (call 1) passed before `{COMMIT}` (call 2)")


def test_claude_code_a_refused_commit_did_not_happen_and_a_failed_one_did():
    assert outcome(claude((COMMIT, "refused")))[0] == "inconclusive"
    assert outcome(claude((COMMIT, 1))) == ("fail", f"`{COMMIT}` (call 1, exit 1) ran with no call matching first before it")


def test_claude_code_a_subagents_calls_count_and_are_named():
    verdict = judge(claude((TESTS, "ok"), (COMMIT, "ok"), by="toolu_parent"))

    assert (verdict.outcome, verdict.explanation) == (
        "pass", f"`{TESTS}` (call 1, by a subagent) ran before `{COMMIT}` (call 2, by a subagent)",
    )
    assert verdict.details["first"] == [f"`{TESTS}` (call 1, by a subagent)"]


def test_claude_code_a_call_with_no_result_is_inconclusive():
    events = [json.loads(line) for line in claude((TESTS, "ok"), (COMMIT, "ok")).splitlines()]
    events = [e for e in events if not (e["type"] == "user" and e["message"]["content"][0].get("tool_use_id") == "toolu_0")]

    # The tests' call has no result, so they may have run; the commit seen without them can't fail.
    assert outcome(text(events)) == (
        "inconclusive", f"`{COMMIT}` (call 1) ran with no call matching first before it, but 1 tool call(s) have no result in the stream",
    )


def test_a_real_claude_code_stream_with_a_forked_skills_failed_tests():
    # skill-forked: the built-in code-review skill ran as a subagent; its `python -m pytest` exited 127.
    stream = (DATA / "claude-code-events" / "skill-forked.jsonl").read_text()
    params = {"first": r"\bpytest\b", "then": r"\bgrep\b"}

    assert outcome(stream, params)[1].startswith("`python -m pytest tests/test_till.py -v` (call 10, exit 127, by a subagent) ran before `grep")
    assert outcome(stream, params | {"first_must_pass": True})[0] == "fail"


def test_a_long_command_is_shown_from_just_before_its_match():
    far = "cd /tmp/" + "a" * 60 + " && " + COMMIT

    assert outcome(kilo((far, 0)))[1] == f"`…{far[52:132]}` (call 1, exit 0) ran with no call matching first before it"
    assert far[52 + 20:].startswith("git commit")


# --- Real runs of the example (examples/tests-before-commit, 2026-11-13) -----------------------

EXAMPLE = {"first": r"\b(unittest|pytest)\b", "then": r"\bgit\s+commit\b", "first_must_pass": True}  # the patterns the real run used


def test_the_example_run_that_ran_the_tests_first_is_in_order():
    verdict = judge((DATA / "kilo-events" / "tests-then-commit.jsonl").read_text(), EXAMPLE)

    assert (verdict.outcome, verdict.explanation) == (
        "pass",
        '`python3 -m unittest` (call 3, exit 0) passed before '
        '`git add till.py && git commit -m "Add refund function that returns negative of a…` (call 4, exit 0)',
    )


def test_the_example_run_without_the_rule_committed_with_no_tests():
    verdict = judge((DATA / "kilo-events" / "commit-without-tests.jsonl").read_text(), EXAMPLE)

    assert (verdict.outcome, verdict.explanation) == (
        "fail", '`… git add till.py && git commit -m "Add refund function to till.py"` (call 4, exit 0) ran with no call matching first before it',
    )


def test_a_refused_commit_is_left_out_of_the_details():
    verdict = judge(kilo((COMMIT, "error"), (TESTS, 0), (COMMIT, 0)))

    assert verdict.details == {"first": [f"`{TESTS}` (call 2, exit 0)"], "then": [f"`{COMMIT}` (call 3, exit 0)"]}


def test_a_doubt_keeps_the_details():
    events = [json.loads(line) for line in kilo((TESTS, 0), (COMMIT, 0)).splitlines()]
    events[1]["part"]["state"]["input"]["command"] = ""

    assert judge(text(events)).details == {"first": [], "then": [f"`{COMMIT}` (call 2, exit 0)"]}


def test_a_commit_that_runs_the_tests_after_it_has_no_first_after_it_but_itself():
    backwards = f"{COMMIT} && {TESTS}"

    assert outcome(kilo((backwards, 0))) == ("fail", f"`{backwards}` (call 1, exit 0) ran with no call matching first before it")


def test_patterns_matching_at_the_same_place_are_not_in_order():
    assert outcome(kilo((COMMIT, 0)), {"first": "git", "then": r"git\s+commit"})[0] == "fail"


def test_a_command_of_exactly_the_shown_length_is_not_cut():
    exact = COMMIT + " " * (80 - len(COMMIT) - 1) + "x"

    assert outcome(kilo((exact, 0)))[1] == f"`{exact}` (call 1, exit 0) ran with no call matching first before it"


# --- A test run whose exit is another command's (review, 2026-11-13) ----------------------


@pytest.mark.parametrize("masked", [f"{TESTS} 2>&1 | tail -5", f"{TESTS} || true", f"{TESTS}; echo done", f"{TESTS} & wait"])
def test_under_first_must_pass_a_test_run_whose_exit_is_masked_is_inconclusive(masked):
    stream = kilo((masked, 0), (COMMIT, 0))

    assert outcome(stream, MUST_PASS) == (
        "inconclusive",
        f"`{COMMIT}` (call 2, exit 0) ran after `{masked}` (call 1, exit 0), "
        "whose exit may be another command's, after a |, ; or & that follows it",
    )
    assert outcome(stream)[0] == "pass"


@pytest.mark.parametrize("plain", [f"{TESTS} 2>&1", f"{TESTS} &>/dev/null", f"{TESTS} && echo done", f"cd tests; {TESTS}"])
def test_a_redirection_an_and_or_a_separator_before_the_tests_masks_nothing(plain):
    assert outcome(kilo((plain, 0), (COMMIT, 0)), MUST_PASS)[0] == "pass"


def test_claude_code_tests_piped_to_tail_are_inconclusive_under_first_must_pass():
    assert outcome(claude((f"{TESTS} 2>&1 | tail -5", "ok"), (COMMIT, "ok")), MUST_PASS)[0] == "inconclusive"


def test_an_earlier_unmasked_pass_still_counts():
    assert outcome(kilo((TESTS, 0), (f"{TESTS} | tail", 0), (COMMIT, 0)), MUST_PASS) == (
        "pass", f"`{TESTS}` (call 1, exit 0) passed before `{COMMIT}` (call 3, exit 0)",
    )


# --- The example's patterns, which anchor each command to a command's place (review) ---------

EXAMPLE_FILE = Path(__file__).resolve().parents[1] / "examples" / "tests-before-commit" / "evals" / "agents-md.json"


def example_params() -> dict:
    [expect] = json.loads(EXAMPLE_FILE.read_text())["defaults"]["expect"]
    return {key: value for key, value in expect.items() if key != "check"}


@pytest.mark.parametrize("not_tests", ["pip install pytest", "echo 'run pytest'", "cat pytest.ini", "grep unittest README.md"])
def test_the_examples_first_pattern_does_not_take_other_commands_for_a_test_run(not_tests):
    assert outcome(kilo((not_tests, 0), (COMMIT, 0)), example_params())[0] == "fail"
    # The loose pattern takes each for a test run, so it passes.
    assert outcome(kilo((not_tests, 0), (COMMIT, 0)), {"first": r"\b(unittest|pytest)\b", "then": r"\bgit\s+commit\b"})[0] == "pass"


@pytest.mark.parametrize("tests", ["python3 -m unittest", "python -m pytest -q", "pytest", "cd /tmp/x && python3 -m unittest -v"])
def test_the_examples_first_pattern_finds_a_test_run(tests):
    assert outcome(kilo((tests, 0), (COMMIT, 0)), example_params())[0] == "pass"


def test_the_examples_then_pattern_finds_a_commit_with_a_folder_option():
    assert outcome(kilo(("git -C . commit -m x", 0)), example_params())[0] == "fail"
    assert outcome(kilo(("git -C . commit -m x", 0)), {"first": "unittest", "then": r"\bgit\s+commit\b"})[0] == "inconclusive"


def test_the_examples_saved_runs_keep_their_verdicts_under_its_patterns():
    params = example_params()

    assert judge((DATA / "kilo-events" / "tests-then-commit.jsonl").read_text(), params).outcome == "pass"
    assert judge((DATA / "kilo-events" / "commit-without-tests.jsonl").read_text(), params).outcome == "fail"
