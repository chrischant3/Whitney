import sys
import types
from types import SimpleNamespace

import pytest
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import ComposeConfig, ComposeService, SandboxEnvironmentSpec

from whitney.harbor_tasks import bigcodebench_hard_complete

MODEL = "test-provider/test-model"  # no default model; any provider/model will do here


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError, match="variant must be 'baseline' or 'template'"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, variant="allow_control")


def test_missing_local_dataset_is_named(tmp_path):
    missing = tmp_path / "nowhere"
    with pytest.raises(FileNotFoundError, match=f"Local Harbor dataset directory not found: {missing}"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(missing))


def test_missing_harbor_extra_says_how_to_install_it(tmp_path, monkeypatch):
    for name in ("inspect_harbor", "inspect_harbor._harbor", "inspect_harbor._harbor.converters"):
        monkeypatch.setitem(sys.modules, name, None)  # import fails whether or not the extra is installed
    with pytest.raises(RuntimeError, match=r'pip install -e "\.\[harbor\]"'):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))


@pytest.fixture
def fake_harbor(tmp_path, monkeypatch):
    @scorer(metrics=[mean()])
    def fake_scorer():
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value=1.0)

        return score

    harbor_task = SimpleNamespace(name="bigcode/sample")
    compose = ComposeConfig(
        services={"default": ComposeService(image="python:3.10", command="tail -f /dev/null")}
    )

    def load_local_tasks(path):
        assert path == tmp_path
        return [harbor_task]

    def to_sample(task, sample_id):
        return Sample(
            id=sample_id,
            input="Write a solution. Save to /workspace/solution.py",
            sandbox=SandboxEnvironmentSpec("docker", compose),
            metadata={"task_name": task.name},
        )

    root = types.ModuleType("inspect_harbor")
    root.__path__ = []
    sub = types.ModuleType("inspect_harbor._harbor")
    sub.__path__ = []
    converters = types.ModuleType("inspect_harbor._harbor.converters")
    converters.harbor_task_to_sample = to_sample
    scorer_module = types.ModuleType("inspect_harbor._harbor.scorer")
    scorer_module.harbor_scorer = fake_scorer
    task_module = types.ModuleType("inspect_harbor._harbor.task")
    task_module._disambiguate_sample_ids = lambda tasks: [task.name for task in tasks]
    task_module.load_harbor_tasks = load_local_tasks
    monkeypatch.setitem(sys.modules, "inspect_harbor", root)
    monkeypatch.setitem(sys.modules, "inspect_harbor._harbor", sub)
    monkeypatch.setitem(sys.modules, "inspect_harbor._harbor.converters", converters)
    monkeypatch.setitem(sys.modules, "inspect_harbor._harbor.scorer", scorer_module)
    monkeypatch.setitem(sys.modules, "inspect_harbor._harbor.task", task_module)


def test_harbor_task_uses_kilo_local_artifact_and_networkless_sandbox(tmp_path, fake_harbor):
    template_task = bigcodebench_hard_complete(target="kilo", model=MODEL, variant="template", dataset_path=str(tmp_path))
    baseline_task = bigcodebench_hard_complete(target="kilo", model=MODEL, variant="baseline", dataset_path=str(tmp_path))
    [sample] = list(template_task.dataset)

    assert sample.id == "bigcode/sample"
    assert "solution.py" in sample.input
    assert "/workspace/solution.py" in sample.input
    assert sample.sandbox.config.services["default"].network_mode == "none"
    assert "AGENTS.md" in template_task.metadata["evaluation_assets"]
    assert "AGENTS.md" not in baseline_task.metadata["evaluation_assets"]


def test_harbor_task_has_no_default_model(tmp_path):
    with pytest.raises(TypeError, match="'model'"):
        bigcodebench_hard_complete(dataset_path=str(tmp_path))


def test_claude_code_target_writes_the_solution_without_a_shell(tmp_path, fake_harbor):
    task = bigcodebench_hard_complete(target="claude-code", model="anthropic/claude-haiku-4-5", dataset_path=str(tmp_path))

    assert set(task.metadata["evaluation_assets"]) == {"AGENTS.md"}
    assert task.metadata["harness_permissions"] == {"allow": ["Read", "Edit", "Write"], "deny": ["Bash"]}
