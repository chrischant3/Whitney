"""Claude Code in the Docker sandbox, its model calls served by Inspect's bridge (Phase 5 Task 36a).

What the container is given comes from the spike of 2026-11-11
(docs/spike-findings.md, "Phase 5 Task 36a"); the stream is the one Claude
Code 2.1.322 printed there (fixture docker-bridge-tools).
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser

from whitney import claude_code as claude_module
from whitney import cli, targets, tasks
from whitney.agent import PROJECT_DIR, RUN_STORE_KEY
from whitney.checks import Evidence, registry
from whitney.claude_code import claude_code
from whitney.scoring import run_completed

EVENTS = Path(__file__).parent / "data" / "claude-code-events"
MODEL = "anthropic/claude-haiku-4-5"
EVALS = Path(__file__).parents[1] / "evals"


def recorded(name: str = "docker-bridge-tools") -> str:
    return (EVENTS / f"{name}.jsonl").read_text()


@pytest.fixture
def container(monkeypatch):
    """A fake sandbox and bridge. `ran` holds each command the agent ran in the container, and how."""
    ran: list[dict] = []
    stored: dict = {}
    reply = {"stdout": recorded(), "stderr": "", "returncode": 0}
    version = {"stdout": "2.1.322 (Claude Code)\n", "returncode": 0, "raises": None}
    raises: list[BaseException] = []  # what the `claude -p` call raises, if anything
    bridge_state = AgentState(messages=[ChatMessageUser(content="from the bridge")])

    class Sandbox:
        async def exec(self, cmd, **kwargs):
            ran.append({"cmd": cmd, **kwargs})
            if cmd == ["claude", "--version"]:
                if version["raises"]:
                    raise version["raises"]
                return SimpleNamespace(stdout=version["stdout"], stderr="", returncode=version["returncode"], success=version["returncode"] == 0)
            if cmd[0] == "claude":
                if raises:
                    raise raises[0]
                return SimpleNamespace(success=reply["returncode"] == 0, **reply)
            return SimpleNamespace(stdout="", stderr="", returncode=0, success=True)

    class Bridge:
        def __init__(self, state, model):
            ran.append({"bridge_model": model})

        async def __aenter__(self):
            return SimpleNamespace(port=13131, state=bridge_state)

        async def __aexit__(self, *exc):
            ran.append({"bridge": "closed"})

    monkeypatch.setattr(claude_module, "sandbox_agent_bridge", Bridge)
    monkeypatch.setattr(claude_module, "sandbox", lambda: Sandbox())
    monkeypatch.setattr(claude_module, "store", lambda: SimpleNamespace(set=stored.__setitem__))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return SimpleNamespace(ran=ran, stored=stored, reply=reply, bridge_state=bridge_state, version=version, raises=raises)


def run(agent, prompt="count the lines"):
    return asyncio.run(agent(AgentState(messages=[ChatMessageUser(content=prompt)])))


def the_claude_run(container) -> dict:
    return next(call for call in container.ran if call.get("cmd", [None])[0] == "claude" and "-p" in call["cmd"])


def test_claude_runs_in_the_container_with_the_flags_a_local_run_uses(container):
    permissions = {"allow": ["Bash"], "deny": ["Skill(code-review)"]}

    run(claude_code(model=MODEL, harness_permissions=permissions, execution="docker", api_key_env=None), prompt="--version")

    call = the_claude_run(container)
    assert call["cmd"] == [
        "claude", "-p", "--setting-sources", "project", "--strict-mcp-config",
        "--settings", json.dumps({"permissions": permissions}),
        "--model", "claude-haiku-4-5", "--output-format", "stream-json", "--verbose",
        "--", "--version",
    ]  # fmt: skip
    assert call["cwd"] == PROJECT_DIR
    assert call["timeout"] == 300


def test_the_container_is_pointed_at_the_bridge_and_given_no_real_key(container, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key-on-the-host")

    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    assert the_claude_run(container)["env"] == {
        "HOME": "/tmp/claude-eval-home",
        "ANTHROPIC_BASE_URL": "http://localhost:13131",
        "ANTHROPIC_API_KEY": "sk-none",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    assert "sk-real-key-on-the-host" not in json.dumps(container.ran, default=str)
    assert {"bridge_model": "inspect"} in container.ran  # every request is answered with the eval's --model


def test_the_container_gets_an_empty_home_before_claude_runs(container):
    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    commands = [call["cmd"] for call in container.ran if "cmd" in call]
    assert commands.index(["mkdir", "-p", "/tmp/claude-eval-home"]) < commands.index(the_claude_run(container)["cmd"])


def test_a_docker_run_needs_no_key_variable_of_its_own(container):
    """Inspect answers for the model, so the agent reads no key: cli.missing_credential checks Inspect's."""
    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    assert container.stored[RUN_STORE_KEY]["exit_code"] == 0


def test_the_run_record_is_read_as_a_local_runs_is_and_names_the_containers_claude(container):
    state = run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    record = container.stored[RUN_STORE_KEY]
    assert record["events"] == recorded() and record["stderr"] == "" and record["exit_code"] == 0
    assert record["harness_version"] == "2.1.322 (Claude Code)"
    assert record["ended_early"] is False and record["permission_denials"] == []
    assert record["api_key_source"] == "ANTHROPIC_API_KEY"
    assert record["final_text"].startswith("The file `till.py` has")
    assert run_completed(record)
    assert state is container.bridge_state  # the bridge's record of the conversation, as Kilo's Docker mode returns


def test_a_denied_skill_the_eval_file_switched_off_is_expected_in_docker_too(container):
    container.reply["stdout"] = recorded("skill-denied")

    run(claude_code(model=MODEL, harness_permissions={"deny": ["Skill(release-notes)"]}, execution="docker", api_key_env=None))
    asked_for = container.stored[RUN_STORE_KEY]["ended_early"]
    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    assert asked_for is False
    assert container.stored[RUN_STORE_KEY]["ended_early"] is True


def test_a_claude_that_fails_in_the_container_is_recorded_as_ended_early(container):
    container.reply.update(stdout="", stderr="boom", returncode=1)

    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    record = container.stored[RUN_STORE_KEY]
    assert (record["exit_code"], record["stderr"], record["ended_early"]) == (1, "boom", True)


def test_a_run_that_times_out_in_the_container_is_recorded_as_a_local_one_is(container):
    """Inspect's Docker exec raises on a timeout; a local run records exit 124, so this must too."""
    container.raises.append(TimeoutError())

    run(claude_code(model=MODEL, execution="docker", api_key_env=None, timeout=7))

    record = container.stored[RUN_STORE_KEY]
    assert (record["exit_code"], record["events"], record["stderr"]) == (124, "", "claude -p timed out after 7s")
    assert record["ended_early"] is True and not run_completed(record)
    assert record["harness_version"] == "2.1.322 (Claude Code)"
    assert {"bridge": "closed"} in container.ran


@pytest.mark.parametrize("change, expected", [
    ({"stdout": "2.1.400 (Claude Code)\nand a second line\n"}, "2.1.400 (Claude Code)"),
    ({"stdout": "v" * 300 + "\n"}, "v" * 200),
    ({"stdout": "\n"}, None),
    ({"stdout": "broken", "returncode": 1}, None),
    ({"raises": TimeoutError()}, None),
])
def test_the_containers_version_is_its_first_line_or_nothing(container, change, expected):
    container.version.update(change)

    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    record = container.stored[RUN_STORE_KEY]
    assert record.get("harness_version") == expected
    assert record["exit_code"] == 0  # the run itself went ahead


def test_the_version_check_cant_hang_the_run(container):
    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    version_call = next(call for call in container.ran if call.get("cmd") == ["claude", "--version"])
    assert version_call["timeout"] == 30


def test_the_hosts_key_is_redacted_from_a_docker_runs_record_should_it_ever_appear(container, monkeypatch):
    """The container is never given the key. This is a second line of defence: the record goes into the log."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key-on-the-host")
    container.reply.update(stdout='{"type": "x", "text": "sk-real-key-on-the-host"}\n', stderr="saw sk-real-key-on-the-host")

    run(claude_code(model=MODEL, execution="docker", api_key_env=None))

    record = container.stored[RUN_STORE_KEY]
    assert "sk-real-key-on-the-host" not in json.dumps(record)
    assert record["stderr"] == "saw [redacted]"


@pytest.mark.parametrize("options, message", [
    ({"api_key_env": "TEAM_KEY"}, "api_key_env applies to local execution; in Docker mode Inspect serves --model through the bridge with its own credentials"),
    ({"api_key_env": None, "local_project_files": {"a.py": ""}}, "files staged on the host can't be used in Docker mode, where the project is the container's"),
])
def test_options_that_only_a_local_run_can_use_are_refused_when_the_agent_is_built(options, message):
    with pytest.raises(ValueError, match=f"^{message}$"):
        claude_code(model=MODEL, execution="docker", **options)


def test_an_execution_that_is_neither_is_refused():
    with pytest.raises(ValueError, match="^execution must be 'local' or 'docker'$"):
        claude_code(model=MODEL, execution="podman")


# --- Around the agent: the target, the task, the credential check ----------------------------


def test_the_claude_code_target_accepts_docker():
    targets.check_target("claude-code", execution="docker")

    agent = targets.build_agent("claude-code", MODEL, {"claude-code": {"allow": ["Bash"]}}, execution="docker")

    assert callable(agent)


def test_a_key_variable_named_for_a_claude_code_docker_run_is_refused():
    with pytest.raises(ValueError, match="^api_key_env applies to local execution"):
        targets.build_agent("claude-code", MODEL, {"claude-code": {}}, execution="docker", api_key_env="TEAM_KEY")


def test_each_target_has_its_own_sandbox_image():
    def sandbox_of(target, model):
        task = tasks.scenario_eval("agents-md.json", target=target, model=model, evals_path=str(EVALS), execution="docker")
        return task.sandbox

    claude, kilo = sandbox_of("claude-code", MODEL), sandbox_of("kilo", MODEL)

    assert claude.type == kilo.type == "docker"
    assert Path(claude.config) == claude_module.COMPOSE_FILE and claude.config != kilo.config
    assert claude_module.COMPOSE_FILE.is_file() and (claude_module.COMPOSE_FILE.parent / "Dockerfile").is_file()


def test_the_claude_code_image_pins_claude_code_and_has_no_network():
    folder = claude_module.COMPOSE_FILE.parent
    compose, dockerfile = claude_module.COMPOSE_FILE.read_text(), (folder / "Dockerfile").read_text()

    assert "network_mode: none" in compose and "cap_drop: [ALL]" in compose and "no-new-privileges:true" in compose
    assert "ARG CLAUDE_CODE_VERSION=2.1.322" in dockerfile
    assert '"@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}"' in dockerfile
    assert "image: claude-code-evals-sandbox:2.1.322-" in compose
    users = [line.split()[1] for line in dockerfile.splitlines() if line.startswith("USER ")]
    assert users[-1] == "node"  # the agent doesn't run as root
    assert "FROM node:22-bookworm-slim@sha256:" in dockerfile  # the base image is pinned by digest
    assert "ANTHROPIC" not in dockerfile and "ANTHROPIC" not in compose  # no key is built into the image


def test_a_claude_code_docker_task_records_no_kilo_model_name():
    task = tasks.scenario_eval("agents-md.json", target="claude-code", model=MODEL, evals_path=str(EVALS), execution="docker")

    assert "kilo_cli_model" not in task.metadata
    assert task.metadata["execution"] == "docker"


@pytest.mark.parametrize("api_key_env, environ, expected", [
    (None, {}, "ANTHROPIC_API_KEY is not set; put it in .env or the environment"),
    (None, {"ANTHROPIC_API_KEY": "k"}, None),
    ("TEAM_KEY", {"ANTHROPIC_API_KEY": "k"}, None),  # refused when the agent is built
    ("TEAM_KEY", {"TEAM_KEY": "k"}, "ANTHROPIC_API_KEY is not set; put it in .env or the environment"),
])
def test_a_claude_code_docker_run_needs_inspects_own_anthropic_key(api_key_env, environ, expected):
    assert cli.missing_credential("claude-code", "docker", api_key_env, environ, model=MODEL) == expected


def test_a_docker_run_without_the_anthropic_package_stops_before_anything_runs(monkeypatch):
    """Inspect's bridge needs it to serve Claude Code, and only the `gateway` extra installs it."""
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None if name == "anthropic" else object())

    assert cli.missing_package("claude-code", "docker") == (
        "Docker mode for claude-code needs the `anthropic` package, which Inspect's bridge uses; "
        'install it with the gateway extra: pip install "whitney[gateway]"'
    )
    assert cli.missing_package("claude-code", "local") is None
    assert cli.missing_package("kilo", "docker") is None


def test_a_docker_run_with_the_anthropic_package_has_no_package_problem():
    assert cli.missing_package("claude-code", "docker") is None


def test_whitney_run_exits_2_when_the_package_is_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(cli, "missing_package", lambda target, execution: "needs the package")
    monkeypatch.setattr(cli, "inspect_eval", lambda *a, **k: pytest.fail("ran a task"))
    (tmp_path / "AGENTS.md").write_text("- rule\n")

    code = cli.main([
        "run", "--target", "claude-code", "--execution", "docker", "--model", MODEL,
        "--project-path", str(tmp_path), "--evals-path", str(EVALS), "--only", "agents_md",
        "--log-dir", str(tmp_path / "logs"), "--results-dir", str(tmp_path / "results"),
    ])

    assert code == 2
    assert "whitney: error: needs the package" in capsys.readouterr().err


# --- The checks read a container's stream as they read a local one ---------------------------


@pytest.mark.parametrize("check, params, word", [
    ("skill_not_loaded", {"skill": "release-notes"}, "not_loaded"),
    ("skill_loaded", {"skill": "release-notes"}, "not_loaded"),
    ("subagent_not_delegated", {"agent": "reviewer"}, "not_delegated"),
    ("subagent_delegated", {"agent": "reviewer"}, "not_delegated"),
])
def test_the_checks_read_a_stream_recorded_in_the_container(check, params, word):
    spec = registry()[check]

    verdict = spec.run(Evidence(exit_code=0, events=recorded(), ended_early=False), params)

    assert spec.label(verdict.outcome) == word


def test_the_containers_stream_has_the_shape_of_a_local_one():
    """Same event types, and the same fields on the two events Whitney reads."""

    def shape(name):
        events = [json.loads(line) for line in recorded(name).splitlines()]
        by_type = {(e["type"], e.get("subtype")): set(e) for e in events}
        return by_type[("system", "init")], by_type[("result", "success")], {e["type"] for e in events}

    docker, local = shape("docker-bridge-tools"), shape("skill-not-loaded")

    assert docker[:2] == local[:2]
    assert docker[2] <= local[2]


# --- The harness version of a Docker run comes from the container ----------------------------


def _score(run: dict):
    spec = registry()["no_file_edits"]
    verdict = spec.run(Evidence(exit_code=0, events=recorded()), {})
    return tasks._scenario_score([(spec, verdict)], ["unchanged", "edited", "inconclusive"], [], run, ["2.1.322 (Claude"])


def test_the_score_records_the_harness_version_the_container_gave():
    run = {"events": recorded(), "exit_code": 0, "stderr": "", "harness_version": "2.1.322 (Claude Code)"}

    assert _score(run).metadata["harness_version"] == "[redacted] Code)"  # redacted like the score's other text
    assert "harness_version" not in _score({"events": recorded(), "exit_code": 0, "stderr": ""}).metadata


def _log(log_version, *score_versions):
    """A scenario_eval log whose metadata records `log_version`, with one sample per entry of `score_versions`."""
    metadata = {
        "variant": "template", "variant_role": "template", "eval_file": "agents-md.json", "eval_name": "agents_md", "title": "Rules",
        "labels": ["unchanged", "edited", "inconclusive"], "min_pass_rate": None, "execution": "docker",
        "checks": {"no_file_edits": {"labels": ["unchanged", "edited", "inconclusive"], "judges": "agent", "min_pass_rate": 0.8}},
        "whitney_version": "0.2.0", "harness_version": log_version,
    }
    samples = [
        SimpleNamespace(id="s", error=None, metadata={}, scores={
            "x": SimpleNamespace(answer="unchanged", metadata={} if version is None else {"harness_version": version}),
        })
        for version in score_versions
    ]
    return SimpleNamespace(eval=SimpleNamespace(task="x", metadata=metadata), samples=samples)


def harness_line(*logs) -> str:
    from whitney.report import render_header

    return render_header(list(logs)).splitlines()[0]


def test_the_report_names_the_containers_harness_version_when_the_log_recorded_none():
    line = harness_line(_log(None, "2.1.322 (Claude Code)", "2.1.322 (Claude Code)", None))

    assert line == "Whitney: `0.2.0` · Harness: `2.1.322 (Claude Code)` · Evals: not given"


def test_a_version_the_log_recorded_is_the_one_shown():
    assert "Harness: `2.1.322-host`" in harness_line(_log("2.1.322-host", "something else"))


def test_runs_in_containers_of_different_versions_are_all_named():
    line = harness_line(_log(None, "2.1.322 (Claude Code)"), _log(None, "2.1.400 (Claude Code)"))

    assert "Harness: `2.1.322 (Claude Code)`, `2.1.400 (Claude Code)`" in line


@pytest.mark.parametrize("odd", [7, "", ["2.1.322"]])
def test_a_score_whose_version_isnt_text_is_left_out(odd):
    assert "Harness: not recorded" in harness_line(_log(None, odd))


def test_a_log_with_errored_or_unscored_samples_still_renders():
    log = _log(None, "2.1.322 (Claude Code)")
    log.samples += [SimpleNamespace(id="e", error="boom", metadata={}, scores=None),
                    SimpleNamespace(id="n", error=None, metadata={}, scores={"x": SimpleNamespace(answer="unchanged")})]

    assert "Harness: `2.1.322 (Claude Code)`" in harness_line(log)
