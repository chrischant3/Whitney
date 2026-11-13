"""whitney.checks.stream: one reader of each harness's stream, every tool call in order.

It is judged on the streams Kilo 7.8.18 and 7.8.27 and Claude Code 2.1.322 really
printed (tests/data/kilo-events/ and tests/data/claude-code-events/), and on
those streams changed by hand to show what a different run would print.
"""

import json
from pathlib import Path

from whitney.checks.stream import SHELL, ToolCall, tool_calls

DATA = Path(__file__).parent / "data"


def lines(harness: str, case: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / f"{harness}-events" / f"{case}.jsonl").read_text().splitlines()]


def text(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def kilo(case: str) -> str:
    return (DATA / "kilo-events" / f"{case}.jsonl").read_text()


def claude(case: str) -> str:
    return (DATA / "claude-code-events" / f"{case}.jsonl").read_text()


def blocks(events: list[dict], kind: str) -> list[dict]:
    """Every content block of type `kind` in a Claude Code stream, to change in place."""
    return [
        block
        for event in events if event["type"] in ("assistant", "user")
        for block in event["message"]["content"] if block["type"] == kind
    ]


def kilo_bash(change) -> str:
    """bash-nonzero-exit with `change` applied to its bash call's state."""
    events = lines("kilo", "bash-nonzero-exit")
    for event in events:
        if event["type"] == "tool_use":
            change(event["part"]["state"])
    return text(events)


# --- Kilo ----------------------------------------------------------------------------------


def test_a_kilo_bash_command_that_ran_and_failed_has_its_exit_code():
    read = tool_calls(kilo("bash-nonzero-exit"))

    assert read.harness == "kilo"
    assert read.calls == (
        ToolCall("bash", {"command": "ls does-not-exist", "description": "List non-existent directory"}, True, False, 1, step=1),
    )
    assert read.doubt is None
    assert read.shell() == ("bash", "command") == SHELL["kilo"]


def test_kilo_calls_come_in_the_streams_order_with_each_commands_exit():
    read = tool_calls(kilo("skill-named"))

    assert [call.tool for call in read.calls] == ["skill"] + ["bash"] * 6
    assert [call.exit_code for call in read.calls] == [None, 0, 0, 128, 0, 0, 0]
    assert [call.passed for call in read.calls] == [None, True, True, False, True, True, True]
    assert read.calls[1].input["command"] == "git log --oneline -20"
    assert {call.by for call in read.calls} == {None}
    assert read.doubt is None


def test_a_refused_kilo_call_did_not_run_and_has_no_exit():
    [call] = tool_calls(kilo("bash-deny-pattern")).calls

    assert (call.tool, call.input["command"], call.ran, call.passed, call.exit_code) == ("bash", "touch marker.txt", False, None, None)


def test_a_completed_kilo_task_call_is_a_doubt_since_the_subagents_calls_are_not_in_the_stream():
    read = tool_calls(kilo("subagent-description"))

    assert [(call.tool, call.ran) for call in read.calls] == [("task", True)]
    assert read.doubt == "1 subagent(s) ran, and a subagent's own tool calls aren't in the stream"


def test_a_refused_kilo_task_call_hid_nothing():
    read = tool_calls(kilo("task-deny"))

    assert [(call.tool, call.ran) for call in read.calls] == [("task", False)]
    assert read.doubt is None


def test_a_kilo_bash_command_with_no_whole_number_exit_has_none():
    def without(state):
        del state["metadata"]["exit"]

    for change in (without, lambda state: state["metadata"].update(exit=True), lambda state: state["metadata"].update(exit="0"),
                   lambda state: state.update(metadata="exit 0")):
        [call] = tool_calls(kilo_bash(change)).calls
        assert (call.ran, call.passed, call.exit_code) == (True, None, None)


def test_a_kilo_bash_command_that_exited_0_passed():
    [call] = tool_calls(kilo_bash(lambda state: state["metadata"].update(exit=0))).calls

    assert (call.passed, call.exit_code) == (True, 0)


def test_only_kilos_shell_tool_has_an_exit_code():
    events = lines("kilo", "bash-nonzero-exit")
    events[1]["part"]["tool"] = "read"

    [call] = tool_calls(text(events)).calls

    assert (call.tool, call.ran, call.passed, call.exit_code) == ("read", True, None, None)


def test_what_could_hide_a_kilo_call_is_a_doubt():
    events = lines("kilo", "bash-nonzero-exit")
    shapeless = dict(events[1], part={"type": "tool", "tool": "bash", "state": {"status": "running", "input": {}}})
    hidden = [events[0], events[2]]  # a step that ended in tool calls and shows none

    assert tool_calls("").doubt == "the stream holds no event the parser knows"
    assert tool_calls(text(events) + "\nnot json").doubt == "1 line(s) of the stream couldn't be read as events"
    assert tool_calls(text([*events, shapeless])).doubt == "1 tool call(s) couldn't be read"
    assert tool_calls(text(hidden)).doubt == "1 step(s) ended in tool calls the stream doesn't show"
    # A blank line isn't an event that couldn't be read, and a new step starts with no calls seen.
    assert tool_calls(text(events) + "\n\n").doubt is None
    assert tool_calls(text([*events, *hidden])).doubt == "1 step(s) ended in tool calls the stream doesn't show"


# --- Claude Code ---------------------------------------------------------------------------


def test_a_claude_code_bash_command_that_ran_and_failed_has_its_exit_code():
    read = tool_calls(claude("settings-allow-ran"))

    assert read.harness == "claude-code"
    assert read.calls == (
        ToolCall(
            "Bash", {"command": "git push origin main", "description": "Push to origin main branch"}, True, False, 1,
            error="Exit code 1\nerror: src refspec main does not match any\nerror: failed to push some refs to 'origin'",
            call_id="toolu_01LcXxsaWCzEpaPVeU8s6azS",
        ),
    )
    assert read.doubt is None
    assert read.shell() == ("Bash", "command") == SHELL["claude-code"]


def test_a_claude_code_bash_command_that_worked_passed_with_no_exit_shown():
    [call] = tool_calls(claude("outside-bash-read")).calls

    assert (call.tool, call.ran, call.passed, call.exit_code, call.by) == ("Bash", True, True, None, None)


def test_a_claude_code_call_refused_by_a_rule_or_a_hook_did_not_run():
    [refused] = tool_calls(claude("settings-deny-held")).calls
    hooked = tool_calls(claude("hook-pretooluse-deny")).calls

    assert (refused.ran, refused.passed, refused.exit_code) == (False, None, None)
    assert [(call.tool, call.input.get("command"), call.ran) for call in hooked][:3] == [
        ("Bash", "git log --oneline -10", False), ("Skill", None, True), ("Bash", "git log --oneline -10", True),
    ]


def test_claude_codes_other_tools_pass_nothing_and_an_error_did_not_run():
    calls = tool_calls(claude("skill-denied")).calls

    assert [(call.tool, call.ran, call.passed) for call in calls][:3] == [("Skill", False, None), ("Read", False, None), ("Bash", True, True)]


def test_a_subagents_calls_are_in_claude_codes_order_and_say_who_made_them():
    calls = tool_calls(claude("outside-subagent-bash-read")).calls

    assert [(call.tool, call.by) for call in calls] == [("Agent", None), ("Bash", "the subagent general-purpose")]


def test_a_forked_skills_failed_test_run_has_its_exit_code():
    calls = tool_calls(claude("skill-forked")).calls
    [tests] = [call for call in calls if "pytest" in str(call.input.get("command"))]

    assert (tests.ran, tests.passed, tests.exit_code, tests.by) == (True, False, 127, "a subagent")
    # The skill's own call comes first: the order is the calls', not their results'.
    assert (calls[0].tool, calls[0].by) == ("Skill", None)


def test_only_claude_codes_shell_tool_reads_exit_code_in_an_error():
    events = lines("claude-code", "docker-bridge-tools")
    [read_result] = [block for block in blocks(events, "tool_result") if "def total" in str(block["content"])]
    read_result.update(is_error=True, content="Exit code 1\nno such file")

    calls = tool_calls(text(events)).calls

    assert sorted((call.tool, call.ran, call.passed, call.exit_code) for call in calls) == [("Bash", True, True, None), ("Read", False, None, None)]


def test_a_claude_code_exit_code_is_read_from_text_blocks_and_from_a_result_with_no_output():
    for content in ([{"type": "text", "text": "Exit code 2\nfailed"}], "Exit code 3"):
        events = lines("claude-code", "settings-allow-ran")
        blocks(events, "tool_result")[0]["content"] = content
        [call] = tool_calls(text(events)).calls
        assert (call.ran, call.exit_code) == (True, 2 if isinstance(content, list) else 3)


def test_an_exit_code_must_begin_the_result_and_end_its_line():
    for content in ("error\nExit code 1\n", "Exit code 12a\n"):
        events = lines("claude-code", "settings-allow-ran")
        blocks(events, "tool_result")[0]["content"] = content
        [call] = tool_calls(text(events)).calls
        assert (call.ran, call.exit_code) == (False, None)


def test_what_could_hide_a_claude_code_call_is_a_doubt():
    def doubt(change) -> str | None:
        events = lines("claude-code", "settings-allow-ran")
        change(events)
        return tool_calls(text(events)).doubt

    def no_result(events):
        events.remove(next(e for e in events if e["type"] == "user" and blocks([e], "tool_result")))

    def orphan(events):
        blocks(events, "tool_use")[0]["id"] = "toolu_other"

    def shapeless(events):
        del blocks(events, "tool_use")[0]["id"]

    def bad_result(events):
        blocks(events, "tool_result")[0]["is_error"] = "yes"

    def bad_parent(events):
        next(e for e in events if e["type"] == "user")["parent_tool_use_id"] = 5

    def bad_blocks(events):
        next(e for e in events if e["type"] == "user")["message"]["content"] = "text"

    assert doubt(no_result) == "1 tool call(s) have no result in the stream"
    assert doubt(orphan) == "1 tool call(s) have no result in the stream"
    assert doubt(lambda events: events.insert(1, {"type": "step_start"})) == "1 line(s) of the stream couldn't be read as events"
    assert doubt(shapeless) == "1 tool call(s) or message(s) couldn't be read"
    assert doubt(bad_result) == "1 tool call(s) or message(s) couldn't be read"
    assert doubt(bad_parent) == "1 tool call(s) or message(s) couldn't be read"
    assert doubt(bad_blocks) == "1 tool call(s) or message(s) couldn't be read"


def test_a_claude_code_call_with_no_result_is_left_out_and_a_result_with_no_call_is_a_doubt():
    events = lines("claude-code", "settings-allow-ran")
    blocks(events, "tool_result")[0]["tool_use_id"] = "toolu_other"
    no_call = lines("claude-code", "settings-allow-ran")
    no_call.remove(next(e for e in no_call if e["type"] == "assistant" and blocks([e], "tool_use")))

    assert tool_calls(text(events)).calls == ()
    assert tool_calls(text(no_call)).doubt == "1 result(s) are of tool calls the stream doesn't show"


def test_a_refused_kilo_call_has_no_exit_whatever_its_state_holds():
    def refused(state):
        state["status"] = "error"

    [call] = tool_calls(kilo_bash(refused)).calls

    assert (call.ran, call.passed, call.exit_code) == (False, None, None)


def test_reading_goes_on_past_a_line_or_call_it_cannot_read():
    events = lines("kilo", "bash-nonzero-exit")
    shapeless = dict(events[1], part={"type": "tool", "tool": "bash", "state": {"status": "running", "input": {}}})
    stream = text([events[0], shapeless, shapeless, events[1], *events[2:]])

    read = tool_calls("not json\n" + stream)
    assert [call.input["command"] for call in read.calls] == ["ls does-not-exist"]
    assert tool_calls(stream).doubt == "2 tool call(s) couldn't be read"


def test_claude_code_reading_goes_on_past_what_it_cannot_read():
    events = lines("claude-code", "settings-allow-ran")
    use_at = next(i for i, e in enumerate(events) if e["type"] == "assistant" and blocks([e], "tool_use"))
    bad_parent = dict(events[use_at], parent_tool_use_id=5)
    shapeless = json.loads(json.dumps(events[use_at]))
    del shapeless["message"]["content"][0]["id"]
    stream = events[:use_at] + [{"type": "step_start"}, bad_parent, shapeless, shapeless] + events[use_at:]

    read = tool_calls(text(stream))
    assert [call.input["command"] for call in read.calls] == ["git push origin main"]
    assert read.doubt == "1 line(s) of the stream couldn't be read as events"
    assert tool_calls(text(events[:use_at] + [bad_parent, bad_parent] + events[use_at:])).doubt == "2 tool call(s) or message(s) couldn't be read"
    assert tool_calls(text(events[:use_at] + [shapeless, shapeless] + events[use_at:])).doubt == "2 tool call(s) or message(s) couldn't be read"


def test_a_claude_code_call_that_worked_with_another_tool_passes_nothing():
    calls = tool_calls(claude("docker-bridge-tools")).calls

    assert sorted((call.tool, call.ran, call.passed) for call in calls) == [("Bash", True, True), ("Read", True, None)]


def test_a_subagents_refused_call_says_who_made_it():
    calls = tool_calls(claude("outside-subagent-bash-denied")).calls

    assert [(call.tool, call.ran, call.exit_code, call.by) for call in calls] == [
        ("Agent", True, None, None), ("Bash", False, None, "a subagent"),
    ]


# --- What each call carries for later checks (review, 2026-11-13) --------------------------


def test_each_kilo_call_carries_its_step_and_a_refusals_words():
    [refused] = tool_calls(kilo("bash-deny-pattern")).calls
    calls = tool_calls(kilo("skill-named")).calls

    assert refused.error.startswith("The user has specified a rule which prevents you from using this specific tool call.")
    assert (refused.call_id, calls[0].call_id) == (None, None)
    assert {call.error for call in calls} == {None}
    assert [call.step for call in calls] == [1, 2, 2, 2, 3, 4, 4]


def test_a_kilo_error_that_is_not_text_is_left_out():
    def refused(state):
        state.update(status="error", error={"message": "no"})

    [call] = tool_calls(kilo_bash(refused)).calls

    assert (call.ran, call.error) == (False, None)


def test_each_claude_code_call_carries_its_id_and_its_error_text():
    [failed] = tool_calls(claude("settings-allow-ran")).calls
    [refused] = tool_calls(claude("settings-deny-held")).calls
    [worked] = tool_calls(claude("outside-bash-read")).calls

    assert failed.call_id == "toolu_01LcXxsaWCzEpaPVeU8s6azS"
    assert failed.error.startswith("Exit code 1\nerror: src refspec main")
    assert refused.error.startswith("Permission to use Bash with command git push origin main has been denied.")
    assert (worked.error, worked.step) == (None, None)
    assert (refused.call_id, worked.call_id) == ("toolu_015X76mRukQrZseatSDqnoas", "toolu_0138LjpMfxMeuECAeMufLDcM")


def test_a_kilo_call_that_ran_has_no_error_whatever_its_state_holds():
    [call] = tool_calls(kilo_bash(lambda state: state.update(error="left over"))).calls

    assert (call.ran, call.error) == (True, None)
