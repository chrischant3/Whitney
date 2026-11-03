import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.dataset import Sample
from inspect_ai.scorer import Score, Target, mean, scorer
from inspect_ai.solver import TaskState
from inspect_ai.util import ComposeConfig, ComposeService, SandboxEnvironmentSpec
from inspect_ai.util._sandbox.compose import ComposeVolumeMount

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
    return SimpleNamespace(compose=compose)


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


def _manifest(dataset: Path, **files: str) -> None:
    import hashlib
    import json

    digests = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}
    (dataset / "inspect-harbor-manifest.json").write_text(json.dumps({"files": digests}))


def test_the_cache_is_checked_against_its_manifest_before_use(tmp_path, fake_harbor):
    (tmp_path / "task.toml").write_text("[task]\n")
    _manifest(tmp_path, **{"task.toml": "[task]\n"})

    task = bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))
    assert task.metadata["cache_integrity"] == "verified"

    (tmp_path / "task.toml").write_text("[task]\nchanged = true\n")
    with pytest.raises(ValueError, match="changed since it was synced: task.toml"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))


def test_a_cache_without_a_manifest_is_used_but_recorded_as_unverified(tmp_path, fake_harbor):
    task = bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))
    assert task.metadata["cache_integrity"] == "unverified: no manifest"


def test_a_symlink_in_the_cache_is_refused(tmp_path, fake_harbor):
    (tmp_path / "Dockerfile").symlink_to("/etc/hosts")
    with pytest.raises(ValueError, match="Dockerfile is a symlink"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))


@pytest.mark.parametrize(
    "setting, value, named",
    [
        ("privileged", True, "privileged"),
        ("cap_add", ["SYS_ADMIN"], "cap_add"),
        ("devices", ["/dev/kvm"], "devices"),
        ("volumes", ["/Users/someone/.ssh:/root/.ssh"], "bind mount /Users/someone/.ssh"),
        ("volumes", ["./data:/data"], "bind mount ./data"),
        ("volumes", ["~/.aws:/home/node/.aws:ro"], "bind mount ~/.aws"),
        ("volumes", [ComposeVolumeMount(type="bind", source="/var/run/docker.sock", target="/var/run/docker.sock")], "bind mount /var/run/docker.sock"),
    ],
)
def test_a_task_whose_sandbox_reaches_the_host_is_refused(tmp_path, fake_harbor, setting, value, named):
    import re

    setattr(fake_harbor.compose.services["default"], setting, value)
    with pytest.raises(ValueError, match=f"asks for {re.escape(named)}, which would let"):
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))


def test_named_and_anonymous_volumes_are_allowed(tmp_path, fake_harbor):
    named = ComposeVolumeMount(type="volume", source="cache", target="/cache")
    fake_harbor.compose.services["default"].volumes = ["cache:/cache", "/scratch", named]
    [sample] = list(bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path)).dataset)
    assert sample.sandbox.config.services["default"].volumes == ["cache:/cache", "/scratch", named]


def test_files_added_or_removed_since_the_sync_are_refused(tmp_path, fake_harbor):
    (tmp_path / "task.toml").write_text("[task]\n")
    _manifest(tmp_path, **{"task.toml": "[task]\n", "tests/test.sh": "exit 0\n"})
    with pytest.raises(ValueError, match="changed since it was synced: tests/test.sh$"):  # removed
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))

    _manifest(tmp_path, **{"task.toml": "[task]\n"})
    for n in range(7):
        (tmp_path / f"extra{n}.sh").write_text("curl attacker.example\n")
    with pytest.raises(ValueError, match="changed since it was synced: extra0.sh, .*extra4.sh and 2 more$"):  # added
        bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))


def test_a_manifest_from_before_digests_is_used_but_recorded_as_unverified(tmp_path, fake_harbor):
    (tmp_path / "inspect-harbor-manifest.json").write_text('{"package": "bigcode/bigcodebench-hard-complete"}')
    task = bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))
    assert task.metadata["cache_integrity"] == "unverified: the manifest has no file digests; re-sync the cache to add them"


def test_the_summary_of_changed_files_lists_five_then_counts_the_rest(tmp_path, fake_harbor):
    import json

    for count, more in ((5, ""), (6, " and 1 more")):
        (tmp_path / "inspect-harbor-manifest.json").write_text(json.dumps({"files": {}}))
        for n in range(count):
            (tmp_path / f"added{n}.sh").write_text("x\n")
        with pytest.raises(ValueError) as raised:
            bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))
        assert str(raised.value).endswith(f"added4.sh{more}")


def test_a_manifest_is_read_as_utf8_whatever_the_locale(tmp_path, fake_harbor, ascii_locale):
    import hashlib

    (tmp_path / "données.txt").write_text("x\n")
    digest = hashlib.sha256(b"x\n").hexdigest()
    (tmp_path / "inspect-harbor-manifest.json").write_text(f'{{"files": {{"données.txt": "{digest}"}}}}', encoding="utf-8")

    task = bigcodebench_hard_complete(target="kilo", model=MODEL, dataset_path=str(tmp_path))

    assert task.metadata["cache_integrity"] == "verified"
