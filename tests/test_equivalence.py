"""What today's `agents_md` and `kilo_jsonc` stage after setup, pinned as golden data.

Phase 5 Tasks 5 to 7 change how scenarios are set up (setup steps, `remove`
and `set` variants, version 2 eval files). This test is their yardstick: for
every shipped scenario, in every variant and every target a task supports, the
project after setup must be what it was when the golden data was recorded.

Each sample is staged the way an eval stages it, through Inspect's local
sandbox: its files are written into a temporary folder and its setup script
runs there. No model is called and no harness runs. The test runs with a HOME
whose git config blocks every commit with a hook, so a setup that let the
user's git in would fail here rather than be recorded.

What `tests/data/golden/<task>-<target>-<variant>.json` records, per scenario:

- `files`: every file in the sandbox after setup (the project and the folder
  beside it), except `project/.git/`, as its SHA-256 and size. Hashes, not
  contents, keep the fixtures' fake canary tokens out of a second place.
- `git`: the commit count; for the initial commit, its tree (mode, blob id
  and path per file), message and identities; `git status` lines; the
  untracked files (ignored ones included); and the hooks in `.git/hooks`.
- `metadata`: the sample metadata setup decides, `initial_commits`, and the
  `outside_before` snapshot as hashes.

Nothing that depends on the time, the machine or the user is recorded: no
commit ids (they include the commit time), no branch name (it depends on the
git version), no absolute paths and no file modes outside git (they depend on
the umask).

Re-recording is deliberate. The test never writes the golden data; to replace
it, run

    python tests/test_equivalence.py --record

and commit the changed files with a commit message that says why the staged
projects were meant to change. A task that only restructures setup (Tasks 5
to 7) must pass without re-recording.
"""

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from inspect_ai._eval.task.sandbox import sandboxenv_context
from inspect_ai.util import SandboxEnvironmentSpec, sandbox
from inspect_ai.util._sandbox.local import LocalSandboxEnvironment

from whitney.agent import PROJECT_DIR
from whitney.git_env import isolated_git_env
from whitney.target_names import TARGETS
from whitney.tasks import agents_md, kilo_jsonc

GOLDEN_DIR = Path(__file__).parent / "data" / "golden"
MODEL = "anthropic/claude-haiku-4-5"  # building a task needs a model both targets accept; none is called

# Every variant each task accepts; test_each_task_accepts_exactly_these_variants keeps this list honest.
VARIANTS = {agents_md: ("template", "baseline"), kilo_jsonc: ("template", "allow_control")}


def _supports(build, target: str) -> bool:
    # Only the task's own "this target isn't supported" refusal counts; any other error is a bug to see.
    try:
        build(target=target, model=MODEL)
    except ValueError as error:
        if "runs only for the kilo target" in str(error):
            return False
        raise
    return True


def _cases() -> list[tuple]:
    return [
        (build, target, variant)
        for build, variants in VARIANTS.items()
        for target in TARGETS
        if _supports(build, target)
        for variant in variants
    ]


def _golden_path(build, target: str, variant: str) -> Path:
    return GOLDEN_DIR / f"{build.__name__}-{target}-{variant}.json"


def _digest(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    # The same isolation the agent gets, so reading the repo doesn't depend on the user's git config either.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""), **isolated_git_env(project)}
    return subprocess.run(["git", *args], cwd=project, env=env, capture_output=True, text=True)


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    assert result.returncode == 0, result.stderr
    return sorted(result.stdout.splitlines())


def _working_tree(root: Path) -> dict:
    files = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not relative.startswith(f"{PROJECT_DIR}/.git/"):
            files[relative] = _digest(path.read_bytes())
    return files


def _initial_commit(project: Path) -> dict | None:
    # HEAD after setup, which is the initial commit while "commits" is 1; None when setup made none.
    if _git(project, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return None
    header = _git(project, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B").stdout.splitlines()
    return {
        "author": header[0],
        "committer": header[1],
        "message": "\n".join(header[2:]).strip(),
        "tree": _lines(_git(project, "ls-tree", "-r", "--full-tree", "HEAD")),
    }


def _git_state(project: Path) -> dict:
    commits = _git(project, "rev-list", "--count", "HEAD")
    hooks = project / ".git" / "hooks"
    return {
        "commits": int(commits.stdout) if commits.returncode == 0 else 0,
        "initial_commit": _initial_commit(project),
        "status": _lines(_git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")),
        "untracked": _lines(_git(project, "ls-files", "--others")),
        "hooks": sorted(p.name for p in hooks.iterdir()) if hooks.is_dir() else [],
    }


def _setup_metadata(sample) -> dict:
    recorded = {}
    if "initial_commits" in sample.metadata:
        recorded["initial_commits"] = sample.metadata["initial_commits"]
    if "outside_before" in sample.metadata:
        recorded["outside_before"] = {
            name: _digest(text.encode("utf-8")) for name, text in sorted(sample.metadata["outside_before"].items())
        }
    return recorded


async def _staged_sample(sample) -> dict:
    """Stage `sample` in Inspect's local sandbox, run its setup, and describe the result."""
    async with sandboxenv_context("equivalence", SandboxEnvironmentSpec("local"), None, True, sample):
        root = Path(sandbox().as_type(LocalSandboxEnvironment).directory.name)
        return {
            "files": _working_tree(root),
            "git": _git_state(root / PROJECT_DIR),
            "metadata": _setup_metadata(sample),
        }


def staged_projects(build, target: str, variant: str) -> dict:
    """What the task stages for each of its scenarios, keyed by scenario id.

    Args:
        build: the task function, agents_md or kilo_jsonc.
        target: a target the task supports.
        variant: one of the task's variants.

    Returns:
        A JSON-ready dict: scenario id to its files, git state and setup metadata.
    """
    task = build(target=target, model=MODEL, variant=variant)

    async def stage_all() -> dict:
        staged = await asyncio.gather(*(_staged_sample(sample) for sample in task.dataset))
        return {str(sample.id): result for sample, result in zip(task.dataset, staged)}

    return asyncio.run(stage_all())


def _to_json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


@pytest.mark.parametrize(
    "build, target, variant",
    _cases(),
    ids=lambda value: getattr(value, "__name__", value),
)
def test_todays_tasks_stage_what_the_golden_data_records(build, target, variant, home_with_blocking_git_hooks):
    golden = json.loads(_golden_path(build, target, variant).read_text(encoding="utf-8"))

    assert staged_projects(build, target, variant) == golden, (
        "The staged projects differ from tests/data/golden. If that change is intended, re-record with "
        "`python tests/test_equivalence.py --record` and say why in the commit message."
    )


def test_the_golden_data_covers_every_case_and_nothing_else():
    expected = {_golden_path(*case).name for case in _cases()}
    assert {path.name for path in GOLDEN_DIR.glob("*.json")} == expected
    assert len(expected) == 6  # agents_md on both targets and kilo_jsonc on kilo, two variants each


@pytest.mark.parametrize("build", list(VARIANTS))
def test_the_golden_data_covers_every_shipped_scenario(build):
    for target, variant in [(t, v) for (b, t, v) in _cases() if b is build]:
        golden = json.loads(_golden_path(build, target, variant).read_text(encoding="utf-8"))
        assert sorted(golden) == sorted(str(sample.id) for sample in build(target=target, model=MODEL).dataset)


@pytest.mark.parametrize("build, variants", VARIANTS.items(), ids=lambda value: getattr(value, "__name__", ""))
def test_each_task_accepts_exactly_these_variants(build, variants):
    with pytest.raises(ValueError, match=f"^{re.escape(f'variant must be one of {sorted(variants)!r}')}, got 'unknown'$"):
        build(target="kilo", model=MODEL, variant="unknown")


def test_the_golden_data_is_written_as_the_recorder_writes_it():
    # Sorted keys and fixed indentation, so a re-recording diffs only where something changed.
    for path in GOLDEN_DIR.glob("*.json"):
        assert path.read_text(encoding="utf-8") == _to_json(json.loads(path.read_text(encoding="utf-8"))), path.name


def record() -> None:
    """Rewrite every golden file from today's tasks, with an empty HOME so no user git config is read."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as home:
        os.environ["HOME"] = home
        for case in _cases():
            path = _golden_path(*case)
            path.write_text(_to_json(staged_projects(*case)), encoding="utf-8")
            print(f"recorded {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        sys.exit("usage: python tests/test_equivalence.py --record   (rewrites tests/data/golden/; see the module docstring)")
    record()
