import json
import sys
import types
from types import SimpleNamespace

import pytest
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import TaskState

from whitney.interference import ifevalcode

MODEL = "test-provider/test-model"  # no default model; any provider/model will do here


@pytest.fixture
def fake_ifevalcode(tmp_path, monkeypatch):
    @scorer(metrics=[mean()])
    def fake_scorer():
        async def score(state: TaskState, target: Target) -> Score:
            return Score(value=1.0)

        return score

    dataset_file = tmp_path / "ifevalcode.jsonl"
    rows = [
        {
            "id": 101,
            "english_question": "Write a function that returns 1.",
            "check_correctness": "def check_correctness(): assert answer() == 1",
            "check_instruction": "def check_instruction(response): assert '```python' in response",
            "entry_point": ["answer"],
            "programming_language": "python",
            "instruction_types": [],
            "domain_types": [],
        },
        {
            "id": 102,
            "english_question": "Write a function that returns 2.",
            "programming_language": "python",
        },
        {
            "id": 103,
            "english_question": "Write a function that returns 3.",
            "programming_language": "python",
        },
    ]
    dataset_file.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    monkeypatch.setattr("whitney.interference.LOCAL_DATASET", dataset_file)

    package = types.ModuleType("inspect_evals")
    package.__path__ = []
    eval_package = types.ModuleType("inspect_evals.ifevalcode")
    eval_package.__path__ = []
    task_module = types.ModuleType("inspect_evals.ifevalcode.ifevalcode")
    task_module.COMPOSE_FILE = tmp_path / "compose.yaml"
    scorer_module = types.ModuleType("inspect_evals.ifevalcode.scorer")
    scorer_module.ifevalcode_scorer = fake_scorer
    monkeypatch.setitem(sys.modules, "inspect_evals", package)
    monkeypatch.setitem(sys.modules, "inspect_evals.ifevalcode", eval_package)
    monkeypatch.setitem(sys.modules, "inspect_evals.ifevalcode.ifevalcode", task_module)
    monkeypatch.setitem(sys.modules, "inspect_evals.ifevalcode.scorer", scorer_module)


def test_ifevalcode_uses_sanitized_local_data_and_official_scorer(tmp_path, fake_ifevalcode):
    task = ifevalcode(target="kilo", model=MODEL, variant="template", languages=["python"], samples_per_language=1)
    [sample] = list(task.dataset)

    assert task.name == "ifevalcode_template"
    assert sample.id == 101
    assert "Write code in python" in sample.input
    assert sample.input.endswith("Write a function that returns 1.")
    assert task.metadata["chinese_question_column_removed"] is True
    assert task.sandbox.type == "docker"


def test_ifevalcode_has_no_default_model():
    import pytest

    with pytest.raises(TypeError, match="'model'"):
        ifevalcode(variant="template")


def test_claude_code_target_stages_only_its_agents_md(fake_ifevalcode):
    task = ifevalcode(target="claude-code", model="anthropic/claude-haiku-4-5", languages=["python"], samples_per_language=1)

    assert set(task.metadata["evaluation_assets"]) == {"AGENTS.md"}
    assert task.metadata["target"] == "claude-code"
