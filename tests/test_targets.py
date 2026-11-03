"""Each target pairs a harness with its own template, permissions and model route.

Checked by building the tasks without running a model.
"""

from pathlib import Path

import pytest
from inspect_ai._util.registry import registry_info

from whitney.tasks import agents_md, kilo_jsonc

ROOT = Path(__file__).parents[1]
KILO_MODEL = "kilo/anthropic/claude-haiku-4.5"
CLAUDE_MODEL = "anthropic/claude-haiku-4-5"


def _project_files(sample):
    return {path.removeprefix("project/"): text for path, text in sample.files.items() if path.startswith("project/")}


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
    assert files["kilo.jsonc"] == (ROOT / "template" / "kilo" / "kilo.jsonc").read_text()
    assert _agent_name(task).endswith("kilo")
    assert task.metadata["target"] == "kilo"


def test_the_permission_eval_is_kilo_only():
    with pytest.raises(ValueError, match="kilo_jsonc tests kilo.jsonc permissions; it runs only for the kilo target"):
        kilo_jsonc(target="claude-code", model=CLAUDE_MODEL)


def test_claude_code_runs_locally_only():
    with pytest.raises(ValueError, match="the claude-code target runs locally only"):
        agents_md(target="claude-code", model=CLAUDE_MODEL, execution="docker")


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
