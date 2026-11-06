"""The version 1 upgrade gives what the shipped eval files give.

Forks may still hold version 1 eval files, which whitney.evalfile upgrades in
memory. `tests/data/version-1/` holds Whitney's shipped files as they were in
version 1. Built through `agents_md` and `kilo_jsonc` against the shipped
fixtures, they must give the same tasks as the shipped files: the same task
names and metadata, and the same samples (ids, prompts, staged files, setup
scripts and metadata). So the upgrade and the shipped files can't drift apart.

The shipped files' harness_permissions must also be what the wrappers grant.
"""

import shutil
import warnings
from pathlib import Path

import pytest

from whitney import tasks
from whitney.evalfile import load
from whitney.tasks import agents_md, kilo_jsonc
from whitney.validate import validate_evals

ROOT = Path(__file__).parent.parent
VERSION_1 = Path(__file__).parent / "data" / "version-1"
MODEL = "anthropic/claude-haiku-4-5"  # building a task needs a model both targets accept; none is called

CASES = [
    (agents_md, "kilo", "template"),
    (agents_md, "kilo", "baseline"),
    (agents_md, "claude-code", "template"),
    (agents_md, "claude-code", "baseline"),
    (kilo_jsonc, "kilo", "template"),
    (kilo_jsonc, "kilo", "allow_control"),
]


@pytest.fixture
def version_1_evals(tmp_path):
    """An evals root holding the version 1 files and a copy of the shipped fixtures."""
    root = tmp_path / "evals"
    shutil.copytree(VERSION_1, root)
    shutil.copytree(ROOT / "evals" / "fixtures", root / "fixtures")
    return root


def _built(build, target, variant):
    task = build(variant, target=target, model=MODEL)
    samples = [(s.id, s.input, s.files, s.setup, s.metadata) for s in task.dataset]
    return task.name, task.metadata, samples


@pytest.mark.parametrize("build, target, variant", CASES, ids=lambda value: getattr(value, "__name__", str(value)))
def test_the_version_1_files_build_the_same_tasks_as_the_shipped_files(build, target, variant, version_1_evals, monkeypatch):
    shipped = _built(build, target, variant)
    monkeypatch.setattr(tasks, "EVALS_DIR", version_1_evals)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        upgraded = _built(build, target, variant)

    assert upgraded == shipped


def test_the_version_1_files_are_valid_with_only_their_version_warnings(version_1_evals):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        result = validate_evals(version_1_evals, constraints=ROOT / "CONSTRAINTS.md")

    assert result.problems == []
    assert [(w.file.name, w.location) for w in result.warnings] == [("agents-md.json", "version"), ("kilo-jsonc.json", "version")]


def test_the_shipped_files_harness_permissions_are_what_the_wrappers_grant():
    # The wrappers grant their own constants until the generic task reads harness_permissions (plan Task 9),
    # so the shipped files must say the same or a reader of the file is misled. None means Whitney's default.
    agents = load(ROOT / "evals" / "agents-md.json")
    kilo_config = load(ROOT / "evals" / "configuration" / "kilo-jsonc.json")

    assert agents.harness_permissions == tasks.AGENTS_MD_PERMISSIONS
    assert kilo_config.harness_permissions is None
    assert kilo_jsonc(target="kilo", model=MODEL).metadata["harness_permissions"] == tasks.HARNESS_PERMISSIONS
