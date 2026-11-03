"""Smoke tests for the two core scenarios in core_scenarios.md, against real harnesses and models.

They run scripts/run_evals.py as a subprocess, as a user would, with the
smallest useful run (agents_md, one scenario, baseline and template, one
repeat), then check the Inspect logs and the report for evidence that each
scenario happened the way it describes. They cost a few cents per scenario,
so the default `pytest` run leaves them out (see pyproject.toml). Run them on
purpose, naming the models (there is no default model):

    E2E_KILO_MODEL=kilo/anthropic/claude-haiku-4.5 \\
    E2E_CLAUDE_MODEL=anthropic/claude-haiku-4-5 \\
    pytest -m e2e tests/e2e

A missing precondition (CLI, model, key) fails the test with what to fix, so
a run never passes without having exercised the scenario.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from inspect_ai.log import list_eval_logs, read_eval_log

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_evals.py"
KILO_TEMPLATE = ROOT / "template" / "kilo"
CLAUDE_TEMPLATE = ROOT / "template" / "claude-code"
CONCLUSIVE = {"followed", "violated"}  # the agent finished and decided; "inconclusive" means the run ended early


def test_run_the_evals_against_a_model_via_kilo_using_the_kilo_template(tmp_path):
    """Scenario: Run the evals against a model via Kilo using the Kilo template.

    Given I have Kilo configured
    When I run the evals against a model
    Then the evals are run against the model using my Kilo configuration
    """
    # Given I have Kilo configured: the CLI is installed and lists the model from my config.
    model = _required_env("E2E_KILO_MODEL", "a model your Kilo config can reach, e.g. kilo/anthropic/claude-haiku-4.5")
    _require_cli("kilo")
    listed = subprocess.run(["kilo", "models", model.split("/", 1)[0]], capture_output=True, text=True, timeout=120)
    assert model in listed.stdout.split(), f"Kilo doesn't list {model} from your config:\n{listed.stdout[-500:]}{listed.stderr[-500:]}"

    # When I run the evals against the model (no --api-key-env: the connection is my Kilo config's).
    logs, report = _run_evals(tmp_path, "--target", "kilo", "--model", model)

    # Then they ran Kilo on that model with my Kilo configuration and the Kilo template.
    for log in logs.values():
        metadata = log.eval.metadata
        assert _agent(log) == "kilo"
        assert (metadata["target"], metadata["model"], metadata["execution"]) == ("kilo", model, "local")
        assert metadata["model_access"] == {"source": "global Kilo config"}
        assert metadata["evaluation_assets"]["kilo.jsonc"] == (KILO_TEMPLATE / "kilo.jsonc").read_text()
        for sample in log.samples:
            run = sample.store["kilo_run"]
            assert run["exit_code"] == 0, f"Kilo exited {run['exit_code']}: {run['stderr'][-500:]}"
            assert _event_types(run["events"]) & {"step_start", "text"}, "no Kilo events in the transcript"
            assert _answer(sample) in CONCLUSIVE
    assert logs["agents_md_template"].eval.metadata["evaluation_assets"]["AGENTS.md"] == (KILO_TEMPLATE / "AGENTS.md").read_text()
    assert "AGENTS.md" not in logs["agents_md_baseline"].eval.metadata["evaluation_assets"]
    assert report.startswith(f"Target: `kilo` · Model: `{model}`\n")
    assert "global Kilo config" in report


def test_run_the_evals_directly_against_claude_code_using_the_claude_code_template(tmp_path):
    """Scenario: Run the evals directly against Claude Code using the Claude Code template.

    Given I have Claude Code installed and ANTHROPIC_API_KEY set
    When I run the evals against an Anthropic model
    Then the evals are run against the model through the Anthropic API,
         using only the Claude Code template, and Kilo is not used at all
    """
    # Given Claude Code is installed and an Anthropic API key is set (shell or .env).
    model = _required_env("E2E_CLAUDE_MODEL", "an Anthropic model, e.g. anthropic/claude-haiku-4-5")
    assert model.startswith("anthropic/"), f"E2E_CLAUDE_MODEL must be anthropic/<model>, got {model!r}"
    _require_cli("claude")
    assert os.environ.get("ANTHROPIC_API_KEY") or _dotenv_names() >= {"ANTHROPIC_API_KEY"}, (
        "ANTHROPIC_API_KEY is not set in the environment or .env"
    )
    # A trap first on PATH: if anything runs `kilo`, the call is recorded and fails.
    trap_dir, trap_log = tmp_path / "trap-bin", tmp_path / "kilo-was-called"
    trap_dir.mkdir()
    (trap_dir / "kilo").write_text(f'#!/bin/sh\necho "$@" >> "{trap_log}"\nexit 1\n')
    (trap_dir / "kilo").chmod(0o755)

    # When I run the evals against the model.
    logs, report = _run_evals(
        tmp_path, "--target", "claude-code", "--model", model, path_prefix=trap_dir
    )

    # Then Claude Code ran on the Anthropic API with my key, the Claude Code template only, and no Kilo.
    assert not trap_log.exists(), f"Kilo was called: {trap_log.read_text()}"
    for log in logs.values():
        metadata = log.eval.metadata
        assert _agent(log) == "claude_code"
        assert (metadata["target"], metadata["model"], metadata["execution"]) == ("claude-code", model, "local")
        assert metadata["model_access"] == {"api_key_env": "ANTHROPIC_API_KEY", "base_url": None}
        assert "kilo.jsonc" not in metadata["evaluation_assets"]
        for sample in log.samples:
            run = sample.store["kilo_run"]
            assert run["exit_code"] == 0 and not run["ended_early"], f"Claude Code ended early: {run['stderr'][-500:]}"
            assert run["api_key_source"] == "ANTHROPIC_API_KEY", "Claude Code didn't use the API key"
            assert "init" in _event_subtypes(run["events"], "system"), "no Claude Code init event in the transcript"
            assert _answer(sample) in CONCLUSIVE
    assert logs["agents_md_template"].eval.metadata["evaluation_assets"] == {
        "AGENTS.md": (CLAUDE_TEMPLATE / "AGENTS.md").read_text()
    }
    assert logs["agents_md_baseline"].eval.metadata["evaluation_assets"] == {}
    assert report.startswith(f"Target: `claude-code` · Model: `{model}`\n")
    assert "key from `ANTHROPIC_API_KEY`" in report


def _run_evals(tmp_path: Path, *args: str, path_prefix: Path | None = None) -> tuple[dict, str]:
    """Run the smallest agents_md eval through the runner; return its logs by task name and the report."""
    env = dict(os.environ)
    if path_prefix:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env.get('PATH', '')}"
    command = [
        sys.executable, str(RUNNER), *args,
        "--evals", "agents_md", "--limit", "1", "--epochs", "1",
        "--log-dir", str(tmp_path / "logs"), "--results-dir", str(tmp_path / "results"),
    ]  # fmt: skip
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    # Exit 1 can just mean the model broke the rule in this one scenario; the logs say whether the runs worked.
    assert result.returncode in (0, 1), f"run_evals.py failed ({result.returncode}):\n{result.stderr[-2000:]}"
    logs = {log.eval.task: log for log in (read_eval_log(info.name) for info in list_eval_logs(str(tmp_path / "logs")))}
    assert set(logs) == {"agents_md_baseline", "agents_md_template"}, f"unexpected logs: {sorted(logs)}\n{result.stderr[-2000:]}"
    for log in logs.values():
        assert log.status == "success", f"{log.eval.task}: {log.status} {log.error}"
        assert log.samples, f"{log.eval.task}: no samples ran"
    [report] = (tmp_path / "results").iterdir()
    return logs, report.read_text()


def _required_env(name: str, what: str) -> str:
    value = os.environ.get(name)
    assert value, f"set {name} to {what}"
    return value


def _require_cli(name: str) -> None:
    assert shutil.which(name), f"`{name}` isn't on PATH; install it first"


def _dotenv_names() -> set[str]:
    """Variable names defined in the repo's .env (values are never read into the test output)."""
    dotenv = ROOT / ".env"
    if not dotenv.is_file():
        return set()
    lines = (line.strip() for line in dotenv.read_text().splitlines())
    return {line.split("=", 1)[0].removeprefix("export ").strip() for line in lines if "=" in line and not line.startswith("#")}


def _agent(log) -> str:
    return log.plan.steps[0].solver


def _answer(sample) -> str:
    return next(iter(sample.scores.values())).answer


def _events(stream: str) -> list[dict]:
    events = []
    for line in stream.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _event_types(stream: str) -> set[str]:
    return {event.get("type") for event in _events(stream)}


def _event_subtypes(stream: str, event_type: str) -> set[str]:
    return {event.get("subtype") for event in _events(stream) if event.get("type") == event_type}
