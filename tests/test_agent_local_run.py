"""The local Kilo runner, driven end to end against a fake `kilo` on PATH.

The fake records what it was run with (arguments, working directory, the
project files it could see), prints a JSON event stream, and can be told to
fail, hang, or write an output file, so these tests check what the runner
actually does with a real subprocess.
"""

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser

from whitney import agent as agent_module
from whitney.agent import RUN_STORE_KEY, kilo, local_config_overlay

MODEL = "test-provider/test-model"  # no default model; any provider/model will do here

FAKE_KILO = r"""#!/bin/sh
printf '%s\n' "$@" > "$PWD/args.txt"
pwd > "$PWD/where.txt"
[ -n "$FAKE_SLEEP" ] && sleep "$FAKE_SLEEP"
[ -f sub/dir/x.txt ] && cp sub/dir/x.txt seen.txt
[ -n "$FAKE_COMMIT" ] && { git init -q && git add -A && git commit -qm "agent commit"; echo "$?" > commit_exit.txt; }
[ -n "$FAKE_WRITE" ] && printf 'def answer():\n    return 42\n' > "$FAKE_WRITE"
echo '{"type":"step_start"}'
echo 'not json'
echo '{"type":"text","part":{"text":"first draft"}}'
echo '{"type":"text","part":{"text":"final answer"}}'
printf '\377\n'
echo "stderr from kilo" >&2
exit "${FAKE_EXIT:-0}"
"""


class FakeSandbox:
    """Stands in for Inspect's sandbox: `pwd` returns a host folder, writes are recorded."""

    def __init__(self, project: Path) -> None:
        self.project = project
        self.writes: list[tuple[str, str]] = []
        self.execs: list[tuple[list[str], dict]] = []

    async def exec(self, command, **kwargs):
        self.execs.append((command, kwargs))
        return SimpleNamespace(stdout=f"{self.project}\n", returncode=0, stderr="")

    async def write_file(self, path, content):
        self.writes.append((path, content))


@pytest.fixture
def fake_kilo(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kilo").write_text(FAKE_KILO)
    (bin_dir / "kilo").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    for name in ("FAKE_SLEEP", "FAKE_EXIT", "FAKE_WRITE", "FAKE_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    sandbox_project = tmp_path / "sandbox-project"
    sandbox_project.mkdir()
    sbox = FakeSandbox(sandbox_project)
    stored: dict = {}
    monkeypatch.setattr(agent_module, "sandbox", lambda: sbox)
    monkeypatch.setattr(agent_module, "store", lambda: SimpleNamespace(set=stored.__setitem__))
    return SimpleNamespace(sandbox=sbox, stored=stored)


def _run(agent, prompt="commit my work"):
    return asyncio.run(agent(AgentState(messages=[ChatMessageUser(content="earlier"), ChatMessageUser(content=prompt)])))


def test_staged_project_run_records_kilo_output_and_copies_declared_outputs(fake_kilo, monkeypatch):
    monkeypatch.setenv("FAKE_WRITE", "solution.py")
    seen = {}
    agent = kilo(
        model="openrouter/anthropic/claude-haiku-4-5",
        local_project_files={"AGENTS.md": "rules", "sub/dir/x.txt": "nested file"},
        local_output_targets={"solution.py": "/workspace/solution.py", "missing.py": "/workspace/missing.py"},
        auto=True,
        timeout=30,
    )
    original = agent_module._run_local_process

    async def spy(project_dir, *args):
        result = await original(project_dir, *args)
        seen["args"] = (project_dir / "args.txt").read_text().splitlines()
        seen["project"] = project_dir
        seen["nested"] = (project_dir / "seen.txt").read_text()
        return result

    monkeypatch.setattr(agent_module, "_run_local_process", spy)

    state = _run(agent)

    run = fake_kilo.stored[RUN_STORE_KEY]
    assert seen["args"] == [
        "run", "--pure", "--format", "json", "-m", "openrouter/anthropic/claude-haiku-4-5", "--auto", "commit my work"
    ]
    assert seen["project"].name == "project" and seen["project"].parent.name.startswith("kilo-eval-")
    assert not seen["project"].exists()  # the staged project is temporary
    assert seen["nested"] == "nested file"
    assert run["exit_code"] == 0
    assert '{"type":"text","part":{"text":"final answer"}}' in run["events"]
    assert "�" in run["events"]  # undecodable bytes are replaced, not fatal
    assert run["stderr"] == "stderr from kilo\n"
    assert fake_kilo.sandbox.writes == [("/workspace/solution.py", "def answer():\n    return 42\n")]
    assert run["copied_outputs"] == ["/workspace/solution.py"]
    assert run["missing_outputs"] == ["missing.py"]
    assert state.messages[-1].text == "final answer"
    assert state.messages[-1].model == "openrouter/anthropic/claude-haiku-4-5"


def test_without_staged_files_kilo_runs_in_the_sandbox_project_without_auto(fake_kilo, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "1")
    _run(kilo(model="anthropic/claude-haiku-4-5", timeout=30), prompt="read ../outside")

    project = fake_kilo.sandbox.project
    assert fake_kilo.sandbox.execs[0] == (["pwd"], {"cwd": "project"})
    assert Path((project / "where.txt").read_text().strip()).resolve() == project.resolve()
    assert (project / "args.txt").read_text().splitlines() == [
        "run", "--pure", "--format", "json", "-m", "anthropic/claude-haiku-4-5", "read ../outside"
    ]
    assert fake_kilo.stored[RUN_STORE_KEY]["exit_code"] == 1  # recorded as evidence, not raised


def test_the_users_git_hooks_and_identity_stay_out_of_kilos_commits(fake_kilo, home_with_blocking_git_hooks, monkeypatch):
    # Kilo keeps the user's HOME (its login lives there), but not their git config: a global
    # secret-scanning hook would otherwise block a bad commit and credit the model with it.
    monkeypatch.setenv("FAKE_COMMIT", "1")
    _run(kilo(model="anthropic/claude-haiku-4-5", timeout=30))

    project = fake_kilo.sandbox.project
    assert (project / "commit_exit.txt").read_text().strip() == "0"
    author = subprocess.run(["git", "log", "-1", "--format=%an <%ae>"], cwd=project, capture_output=True, text=True)
    assert author.stdout.strip() == "Eval Agent <agent@example.invalid>"


def test_a_hung_kilo_is_killed_and_recorded_as_a_timeout(fake_kilo, monkeypatch):
    import time

    monkeypatch.setenv("FAKE_SLEEP", "10")  # a child of kilo (like a tool's shell command) that outlives the timeout
    started = time.monotonic()
    state = _run(kilo(model="anthropic/claude-haiku-4-5", timeout=1))

    assert time.monotonic() - started < 5  # the whole process group is killed, so the run ends at the timeout

    assert fake_kilo.stored[RUN_STORE_KEY] == {"exit_code": 124, "events": "", "stderr": "kilo run timed out after 1s"}
    assert state.messages[-1].text == "commit my work"  # no final message from a timed-out run


def test_a_cancelled_run_stops_kilo_and_the_tools_it_started(fake_kilo, monkeypatch, tmp_path):
    # Ctrl-C, an Inspect time limit or another sample's error cancels the run. Kilo is in its
    # own session, so nothing but the runner can stop it.
    marker = tmp_path / "outlived-the-cancel.txt"
    monkeypatch.setenv("FAKE_SLEEP", "2")
    monkeypatch.setenv("FAKE_WRITE", str(marker))  # written after the sleep, only if kilo is still running
    started = fake_kilo.sandbox.project / "args.txt"

    async def cancel_mid_run():
        state = AgentState(messages=[ChatMessageUser(content="commit my work")])
        run = asyncio.create_task(kilo(model="anthropic/claude-haiku-4-5", timeout=30)(state))
        for _ in range(100):  # wait until kilo is running, so the cancel lands mid-run
            if started.exists():
                break
            await asyncio.sleep(0.05)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        await asyncio.sleep(3)  # longer than kilo's sleep

    asyncio.run(cancel_mid_run())

    assert started.exists()
    assert not marker.exists()


@pytest.mark.parametrize("path", ["/etc/passwd", "../escape.txt", "a/../../b.txt"])
def test_staged_files_must_stay_inside_the_project(fake_kilo, path):
    with pytest.raises(ValueError, match=f"local project file path must be relative: '{path}'"):
        _run(kilo(model=MODEL, local_project_files={path: "x"}))


@pytest.mark.parametrize(
    "mapping",
    [{"/abs/solution.py": "/workspace/solution.py"}, {"../solution.py": "/workspace/solution.py"}, {"solution.py": "relative/target.py"}],
)
def test_output_mappings_need_a_relative_source_and_absolute_target(fake_kilo, mapping):
    with pytest.raises(ValueError, match="^local output mapping must use a relative source and absolute sandbox target$"):
        _run(kilo(model=MODEL, local_project_files={"AGENTS.md": "x"}, local_output_targets=mapping))


def test_harness_permissions_reach_kilo_through_a_temporary_config(fake_kilo, monkeypatch):
    seen = {}
    original = agent_module._run_local_process

    async def spy(project_dir, state, kilo_model, auto, env, timeout):
        seen["config"] = Path(env["KILO_CONFIG"]).read_text()
        seen["path"] = env["KILO_CONFIG"]
        return await original(project_dir, state, kilo_model, auto, env, timeout)

    monkeypatch.setattr(agent_module, "_run_local_process", spy)
    _run(kilo(model=MODEL, harness_permissions={"bash": "allow"}, local_project_files={"AGENTS.md": "x"}, timeout=30))

    assert seen["config"] == '{"permission": {"bash": "allow"}}'
    assert not Path(seen["path"]).exists()  # removed after the run


def test_overlay_uses_the_first_segment_of_a_nested_model_id_as_the_provider():
    overlay = local_config_overlay("openrouter/anthropic/claude", None, api_key_env="K", environ={"K": "v"})
    assert list(overlay["provider"]) == ["openrouter"]


def test_docker_run_executes_kilo_in_the_sandbox_project_and_records_the_result():
    from whitney.agent import _run_kilo

    class Sandbox:
        calls = []

        async def exec(self, command, **kwargs):
            self.calls.append((command, kwargs))
            if command == ["pwd"]:
                return SimpleNamespace(stdout="/workspace/project\n")
            return SimpleNamespace(returncode=3, stdout="events", stderr="oops")

    sbox = Sandbox()
    state = SimpleNamespace(messages=[SimpleNamespace(text="do it")])

    run = asyncio.run(_run_kilo(sbox, state, "anthropic/claude", True, {"HOME": "/root"}, 42))

    assert run == {"exit_code": 3, "events": "events", "stderr": "oops"}
    assert sbox.calls == [
        (["pwd"], {"cwd": "project"}),
        (
            ["kilo", "run", "--pure", "--format", "json", "-m", "anthropic/claude", "--auto", "do it"],
            {"cwd": "project", "env": {"HOME": "/root", "PWD": "/workspace/project"}, "timeout": 42},
        ),
    ]


def test_docker_run_timeout_is_recorded():
    from whitney.agent import _run_kilo

    class Sandbox:
        async def exec(self, command, **kwargs):
            if command == ["pwd"]:
                return SimpleNamespace(stdout="/workspace/project\n")
            raise TimeoutError

    state = SimpleNamespace(messages=[SimpleNamespace(text="x")])
    run = asyncio.run(_run_kilo(Sandbox(), state, "m/x", False, None, 7))
    assert run == {"exit_code": 124, "events": "", "stderr": "kilo run timed out after 7s"}


def test_final_message_is_the_last_text_event_and_absent_without_one():
    from whitney.agent import _append_final_message

    state = AgentState(messages=[ChatMessageUser(content="q")])
    _append_final_message(state, {}, "m/x")
    _append_final_message(state, {"events": 'not json\n{"type":"step"}\n{"type":"text","part":{}}'}, "m/x")
    assert [m.text for m in state.messages] == ["q"]


def test_permissions_reach_kilo_in_the_sandbox_project_too(fake_kilo, monkeypatch):
    seen = {}
    original = agent_module._run_local_process

    async def spy(project_dir, state, kilo_model, auto, env, timeout):
        seen["config"] = Path(env["KILO_CONFIG"]).read_text()
        return await original(project_dir, state, kilo_model, auto, env, timeout)

    monkeypatch.setattr(agent_module, "_run_local_process", spy)
    _run(kilo(model=MODEL, harness_permissions={"edit": "allow"}, timeout=30))
    assert seen["config"] == '{"permission": {"edit": "allow"}}'


def test_staged_project_runs_honour_the_timeout(fake_kilo, monkeypatch):
    monkeypatch.setenv("FAKE_SLEEP", "10")
    _run(kilo(model=MODEL, local_project_files={"AGENTS.md": "x"}, timeout=1))
    assert fake_kilo.stored[RUN_STORE_KEY]["exit_code"] == 124


def test_every_declared_output_is_checked_even_after_a_missing_one(fake_kilo, monkeypatch):
    monkeypatch.setenv("FAKE_WRITE", "solution.py")
    targets = {"missing.py": "/workspace/missing.py", "solution.py": "/workspace/solution.py"}
    _run(kilo(model=MODEL, local_project_files={"AGENTS.md": "x"}, local_output_targets=targets, timeout=30))
    run = fake_kilo.stored[RUN_STORE_KEY]
    assert (run["missing_outputs"], run["copied_outputs"]) == (["missing.py"], ["/workspace/solution.py"])
