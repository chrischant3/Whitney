"""Each target pairs a harness with its own template, permissions and model route.

Checked by building the tasks without running a model.
"""

import asyncio
from pathlib import Path

import pytest
from inspect_ai._eval.task.sandbox import read_sandboxenv_file
from inspect_ai._util.registry import registry_info

from whitney.project_files import load_project_files_for
from whitney.tasks import agents_md, kilo_jsonc

ROOT = Path(__file__).parents[1]
KILO_MODEL = "kilo/anthropic/claude-haiku-4.5"
CLAUDE_MODEL = "anthropic/claude-haiku-4-5"


def _project_files(sample):
    """The project files Inspect writes into the sandbox for `sample`."""
    return {
        path.removeprefix("project/"): asyncio.run(read_sandboxenv_file(text)).decode("utf-8")
        for path, text in sample.files.items()
        if path.startswith("project/")
    }


def _agent_name(task) -> str:
    return registry_info(task.solver).name


def test_claude_code_target_stages_its_own_template_and_runs_claude_code():
    task = agents_md(target="claude-code", model=CLAUDE_MODEL)

    files = _project_files(task.dataset[0])
    assert files["AGENTS.md"] == (ROOT / "template" / "claude-code" / "AGENTS.md").read_text()
    assert "kilo.jsonc" not in files
    assert _agent_name(task).endswith("claude_code")
    assert task.metadata["target"] == "claude-code"
    assert task.metadata["harness_permissions"] == {"allow": ["Bash", "Edit", "Write", "Read"]}
    assert task.metadata["model_access"] == {"api_key_env": "ANTHROPIC_API_KEY", "base_url": None}


def test_claude_code_target_baseline_leaves_out_agents_md_too():
    task = agents_md(variant="baseline", target="claude-code", model=CLAUDE_MODEL)

    assert "AGENTS.md" not in _project_files(task.dataset[0])


def test_claude_code_target_records_a_different_key_variable():
    task = agents_md(target="claude-code", model=CLAUDE_MODEL, api_key_env="TEAM_ANTHROPIC_KEY")

    assert task.metadata["model_access"] == {"api_key_env": "TEAM_ANTHROPIC_KEY", "base_url": None}


def test_kilo_target_stages_the_kilo_template_and_runs_kilo():
    task = agents_md(target="kilo", model=KILO_MODEL)

    files = _project_files(task.dataset[0])
    # The template goes through the Kilo loader, which re-serialises kilo.jsonc without its comments.
    assert files["kilo.jsonc"] == load_project_files_for("kilo", ROOT / "template" / "kilo")["kilo.jsonc"]
    assert _agent_name(task).endswith("kilo")
    assert task.metadata["target"] == "kilo"


def test_the_permission_eval_is_kilo_only():
    with pytest.raises(ValueError, match="kilo_jsonc tests kilo.jsonc permissions; it runs only for the kilo target"):
        kilo_jsonc(target="claude-code", model=CLAUDE_MODEL)


def test_claude_code_runs_in_docker_too():
    """Until Phase 5 Task 36a this was refused: "the claude-code target runs locally only"."""
    task = agents_md(target="claude-code", model=CLAUDE_MODEL, execution="docker")

    assert task.sandbox.type == "docker" and task.sandbox.config.endswith("sandbox/claude-code/compose.yaml")
    assert task.metadata["execution"] == "docker"


def test_claude_code_has_no_base_url_option():
    with pytest.raises(ValueError, match="base_url applies to the kilo target only"):
        agents_md(target="claude-code", model=CLAUDE_MODEL, base_url="https://proxy.example/v1")


def test_unknown_target_is_rejected():
    with pytest.raises(ValueError, match="target must be one of kilo, claude-code; got 'cursor'"):
        agents_md(target="cursor", model=KILO_MODEL)


@pytest.mark.parametrize("build", [agents_md, kilo_jsonc])
def test_there_is_no_default_target(build):
    with pytest.raises(TypeError, match="'target'"):
        build(model=KILO_MODEL)


def test_docker_runs_record_the_model_name_kilo_was_given():
    task = agents_md(target="kilo", model=KILO_MODEL, execution="docker")

    assert task.metadata["kilo_cli_model"] == "anthropic/claude-haiku-4-5"
    assert "kilo_cli_model" not in agents_md(target="kilo", model=KILO_MODEL).metadata


def test_options_a_target_cant_use_are_refused_with_a_reason():
    from whitney import targets

    targets.check_target("claude-code", execution="docker")  # accepted since Phase 5 Task 36a
    with pytest.raises(ValueError, match="^api_key_env applies to local execution; in Docker mode Inspect serves"):
        targets.build_agent("claude-code", CLAUDE_MODEL, {"claude-code": {}}, execution="docker", api_key_env="TEAM_KEY")
    with pytest.raises(ValueError, match="^base_url applies to the kilo target only$"):
        targets.check_target("claude-code", base_url="https://x.example")
    with pytest.raises(ValueError, match="^base_url applies to the kilo target only$"):
        targets.build_agent("claude-code", CLAUDE_MODEL, {"claude-code": {}}, base_url="https://x.example")
    with pytest.raises(ValueError, match="^target must be one of kilo, claude-code; got 'KILO'$"):
        targets.model_access("KILO", None, None)


@pytest.mark.parametrize("target, model", [("kilo", KILO_MODEL), ("claude-code", CLAUDE_MODEL)])
def test_build_agent_hands_every_option_to_the_targets_agent(monkeypatch, target, model):
    from whitney import targets

    built = {}
    for name in ("kilo", "claude_code"):
        monkeypatch.setattr(targets, name, lambda name=name, **kwargs: built.update(agent=name, **kwargs))
    permissions = {"kilo": {"bash": "allow"}, "claude-code": {"allow": ["Read"]}}
    files, outputs = {"AGENTS.md": "x"}, {"solution.py": "/workspace/solution.py"}

    targets.build_agent(
        target, model, permissions, local_project_files=files, local_output_targets=outputs, api_key_env="TEAM_KEY",
        base_url="https://proxy.example/v1" if target == "kilo" else None,
    )  # fmt: skip

    common = {"model": model, "harness_permissions": permissions[target], "local_project_files": files,
              "local_output_targets": outputs, "api_key_env": "TEAM_KEY"}  # fmt: skip
    if target == "kilo":
        assert built == {"agent": "kilo", **common, "execution": "local", "base_url": "https://proxy.example/v1"}
    else:
        assert built == {"agent": "claude_code", **common}


def test_claude_code_defaults_to_the_anthropic_key_variable(monkeypatch):
    from whitney import targets

    built = {}
    monkeypatch.setattr(targets, "claude_code", lambda **kwargs: built.update(kwargs))
    targets.build_agent("claude-code", CLAUDE_MODEL, {"claude-code": {}})
    assert built["api_key_env"] == "ANTHROPIC_API_KEY"
