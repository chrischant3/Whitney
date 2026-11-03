"""The Claude Code agent, driven end to end against a fake `claude` on PATH.

The fake records its arguments and the environment it was given, then prints
a stream-json transcript whose final result event the test chooses, so these
tests check the isolation and outcome handling with a real subprocess. The
agent passes the fake no environment of the test's, so the fake reads its
controls from control.sh beside it.
"""

import asyncio
import json
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser

from whitney import claude_code as claude_module
from whitney.agent import RUN_STORE_KEY
from whitney.claude_code import claude_code
from whitney.scoring import run_completed

MODEL = "anthropic/claude-haiku-4-5"

FAKE_CLAUDE = r"""#!/bin/sh
. "$(dirname "$0")/control.sh"
printf '%s\n' "$@" > "$PWD/args.txt"
{
  echo "HOME=$HOME"
  echo "KEY=$ANTHROPIC_API_KEY"
  echo "USER_SECRET=$USER_SECRET"
  echo "GIT_AUTHOR_NAME=$GIT_AUTHOR_NAME"
  echo "GIT_COMMITTER_EMAIL=$GIT_COMMITTER_EMAIL"
} > "$PWD/env.txt"
ls -A "$HOME" > "$PWD/home.txt"
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
[ -n "$FAKE_WRITE" ] && printf 'def answer():\n    return 42\n' > "$FAKE_WRITE"
echo '{"type":"system","subtype":"init","apiKeySource":"ANTHROPIC_API_KEY"}'
echo '{"type":"system","subtype":"status"}'
echo 'not json'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}'
[ -n "$FAKE_LEAK" ] && printf '{"type":"assistant","message":{"content":[{"type":"text","text":"%s"}]}}\n' "$ANTHROPIC_API_KEY"
[ -n "$FAKE_LEAK" ] && echo "$ANTHROPIC_API_KEY" >&2
[ -n "$FAKE_RESULT" ] && printf '%s\n' "$FAKE_RESULT"
echo "stderr from claude" >&2
exit "${FAKE_EXIT:-0}"
"""

SUCCESS = json.dumps({"type": "result", "subtype": "success", "is_error": False, "permission_denials": [], "result": "Committed app.py."})


class FakeSandbox:
    """Stands in for Inspect's sandbox: `pwd` returns a host folder, writes are recorded."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.writes: list[tuple[str, str]] = []

    async def exec(self, command, **kwargs):
        return SimpleNamespace(stdout=f"{self.project}\n", returncode=0, stderr="")

    async def write_file(self, path, content):
        self.writes.append((path, content))


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("USER_SECRET", "must-not-leak")
    controls = {"FAKE_RESULT": SUCCESS}

    def control(**values):
        controls.update(values)
        lines = [f"{name}={shlex.quote(value)}" for name, value in controls.items()]
        (bin_dir / "control.sh").write_text("\n".join(lines) + "\n")

    control()
    project = tmp_path / "sandbox-project"
    project.mkdir()
    sbox = FakeSandbox(project)
    stored: dict = {}
    monkeypatch.setattr(claude_module, "sandbox", lambda: sbox)
    monkeypatch.setattr(claude_module, "store", lambda: SimpleNamespace(set=stored.__setitem__))
    return SimpleNamespace(sandbox=sbox, project=project, stored=stored, control=control)


def _run(agent, prompt="commit my work"):
    return asyncio.run(agent(AgentState(messages=[ChatMessageUser(content="earlier"), ChatMessageUser(content=prompt)])))


def _env(project: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in (project / "env.txt").read_text().splitlines())


def test_runs_claude_headless_with_only_project_settings_and_no_mcp_servers(fake_claude):
    _run(claude_code(model=MODEL, harness_permissions={"allow": ["Edit"], "deny": ["Bash"]}))

    args = (fake_claude.project / "args.txt").read_text().splitlines()
    assert args[:2] == ["-p", "--setting-sources"] and args[2] == "project"
    assert "--strict-mcp-config" in args
    assert args[args.index("--model") + 1] == "claude-haiku-4-5"
    assert args[args.index("--output-format") + 1] == "stream-json" and "--verbose" in args
    assert json.loads(args[args.index("--settings") + 1]) == {"permissions": {"allow": ["Edit"], "deny": ["Bash"]}}
    assert "--dangerously-skip-permissions" not in args
    assert args[-1] == "commit my work"


def test_without_harness_permissions_nothing_extra_is_allowed(fake_claude):
    _run(claude_code(model=MODEL))

    args = (fake_claude.project / "args.txt").read_text().splitlines()
    assert json.loads(args[args.index("--settings") + 1]) == {"permissions": {}}


def test_user_home_and_environment_stay_out_of_the_run(fake_claude):
    _run(claude_code(model=MODEL))

    env = _env(fake_claude.project)
    assert env["HOME"] != str(Path.home()) and not Path(env["HOME"]).exists()  # a temporary home, removed afterwards
    assert (fake_claude.project / "home.txt").read_text() == ""  # and empty while claude ran
    assert env["KEY"] == "sk-test"
    assert env["USER_SECRET"] == ""
    assert env["GIT_AUTHOR_NAME"] == "Eval Agent" and env["GIT_COMMITTER_EMAIL"] == "agent@example.invalid"


def test_api_key_can_come_from_another_variable(fake_claude, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("TEAM_ANTHROPIC_KEY", "sk-team")

    _run(claude_code(model=MODEL, api_key_env="TEAM_ANTHROPIC_KEY"))

    assert _env(fake_claude.project)["KEY"] == "sk-team"


def test_missing_api_key_is_reported_by_name(fake_claude, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY is not set"):
        _run(claude_code(model=MODEL))


def test_only_anthropic_models_are_accepted():
    with pytest.raises(ValueError, match="anthropic/<model>"):
        claude_code(model="kilo/anthropic/claude-haiku-4.5")


def test_a_successful_run_records_the_transcript_and_the_final_answer(fake_claude):
    state = _run(claude_code(model=MODEL))

    run = fake_claude.stored[RUN_STORE_KEY]
    assert run["exit_code"] == 0 and run["ended_early"] is False
    assert run["api_key_source"] == "ANTHROPIC_API_KEY"
    assert '"result": "Committed app.py."' in run["events"] and "stderr from claude" in run["stderr"]
    assert state.messages[-1].text == "Committed app.py." and state.messages[-1].model == MODEL
    assert run_completed(run)


@pytest.mark.parametrize(
    "result",
    [
        {"type": "result", "subtype": "error_max_turns", "is_error": True, "permission_denials": []},
        {"type": "result", "subtype": "success", "is_error": True, "permission_denials": []},
        {"type": "result", "subtype": "success", "is_error": False, "permission_denials": [{"tool_name": "Bash"}]},
        None,
    ],
    ids=["error", "success-flagged-as-error", "permission-denied", "no-result"],
)
def test_runs_that_end_before_the_agent_finished_are_not_complete(fake_claude, result):
    fake_claude.control(FAKE_RESULT=json.dumps(result) if result else "")

    _run(claude_code(model=MODEL))

    run = fake_claude.stored[RUN_STORE_KEY]
    assert run["exit_code"] == 0 and run["ended_early"] is True
    assert run["permission_denials"] == ((result or {}).get("permission_denials") or [])
    assert not run_completed(run)


def test_timeouts_are_recorded_and_not_complete(fake_claude):
    fake_claude.control(FAKE_SLEEP="10")

    _run(claude_code(model=MODEL, timeout=1))

    run = fake_claude.stored[RUN_STORE_KEY]
    assert run["exit_code"] == 124 and run["stderr"] == "claude -p timed out after 1s"
    assert not run_completed(run)


def test_staged_project_outputs_are_copied_into_the_sandbox(fake_claude):
    fake_claude.control(FAKE_WRITE="solution.py")

    _run(
        claude_code(
            model=MODEL,
            local_project_files={"AGENTS.md": "rules"},
            local_output_targets={"solution.py": "/workspace/solution.py", "missing.py": "/workspace/missing.py"},
        )
    )

    run = fake_claude.stored[RUN_STORE_KEY]
    assert run["copied_outputs"] == ["/workspace/solution.py"] and run["missing_outputs"] == ["missing.py"]
    assert fake_claude.sandbox.writes == [("/workspace/solution.py", "def answer():\n    return 42\n")]


def test_the_api_key_is_redacted_from_what_the_eval_stores(fake_claude, monkeypatch):
    # Claude Code needs the key, so its Bash tool can print it; the transcript goes into the eval log.
    key = "sk-ant-test-key-0123456789"
    monkeypatch.setenv("ANTHROPIC_API_KEY", key)
    leaked_result = json.dumps({**json.loads(SUCCESS), "result": f"The key is {key}"})
    fake_claude.control(FAKE_LEAK="1", FAKE_RESULT=leaked_result)

    state = _run(claude_code(model=MODEL))

    run = fake_claude.stored[RUN_STORE_KEY]
    assert key not in run["events"] and key not in run["stderr"]
    assert run["final_text"] == state.messages[-1].text == "The key is [redacted]"
