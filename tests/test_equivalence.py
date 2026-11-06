"""What today's `agents_md` and `kilo_jsonc` stage after setup, pinned as golden data.

Phase 5 Tasks 5 to 7 change how scenarios are set up (setup steps, `remove`
and `set` variants, version 2 eval files). This test is their yardstick: for
every shipped scenario, in every variant and every target a task supports, the
project after setup must be what it was when the golden data was recorded.

Each sample is staged the way an eval stages it, through Inspect's local
sandbox: its files are written into a temporary folder and its setup script
runs there. No model is called and no harness runs. Both the test and the
recorder run with a HOME whose git config blocks every commit with a hook
(conftest.blocking_git_hooks_home), so a setup that let the user's git in
fails rather than being recorded.

What `tests/data/golden/<task>-<target>-<variant>.json` records, per scenario:

- `files`: every entry in the sandbox after setup (the project and the folder
  beside it), except `project/.git/`: a file as its SHA-256 and size, a
  folder as `{"directory": true}` (an empty one is visible to the agent too),
  a symlink as `{"symlink": <target>}`. Hashes, not contents, keep the
  fixtures' fake canary tokens out of a second place.
- `git`: the commit count; HEAD after setup (the initial commit), with its
  message, identities and tree (per path, its mode and the SHA-256 of its
  content); every ref, with HEAD's branch written as `<HEAD branch>`;
  `git status` lines; the untracked files (ignored ones included); and the
  hooks in `.git/hooks`.
- `metadata`: the sample metadata setup decides: `initial_commits` (a sample
  without it records 0, so missing and 0 are treated the same) and the
  `outside_before` snapshot as hashes.

Nothing that depends on the time, the machine, the user or the git version is
recorded: no commit or object ids (commit ids include the time, and object
ids change if git moves from SHA-1 to SHA-256 objects), no default branch
name, no absolute paths and no file modes outside git (they follow the umask).

Re-recording is deliberate. The test never writes the golden data; to replace
it, run

    python tests/test_equivalence.py --record

which prints what changed, and commit the changed files with a commit message
that says why the staged projects were meant to change. A task that only
restructures setup (Tasks 5 to 7) must pass without re-recording.
"""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from inspect_ai._eval.task.sandbox import sandboxenv_context
from inspect_ai.util import SandboxEnvironmentSpec, sandbox
from inspect_ai.util._sandbox.local import LocalSandboxEnvironment

from conftest import blocking_git_hooks_home
from whitney import tasks as tasks_module
from whitney.agent import PROJECT_DIR
from whitney.git_env import isolated_git_env
from whitney.tasks import agents_md, kilo_jsonc

GOLDEN_DIR = Path(__file__).parent / "data" / "golden"
MODEL = "anthropic/claude-haiku-4-5"  # building a task needs a model both targets accept; none is called
HEAD_BRANCH = "<HEAD branch>"  # git's default branch name depends on its version

# Every task, target and variant today's code supports: kilo_jsonc runs only for kilo.
CASES = [
    (agents_md, "kilo", "template"),
    (agents_md, "kilo", "baseline"),
    (agents_md, "claude-code", "template"),
    (agents_md, "claude-code", "baseline"),
    (kilo_jsonc, "kilo", "template"),
    (kilo_jsonc, "kilo", "allow_control"),
]


def _golden_path(build, target: str, variant: str) -> Path:
    return GOLDEN_DIR / f"{build.__name__}-{target}-{variant}.json"


def _digest(data: bytes) -> dict:
    return {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    # The same isolation the agent gets, so reading the repo doesn't depend on the user's git config either.
    env = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", ""), **isolated_git_env(project)}
    return subprocess.run(["git", *args], cwd=project, env=env, capture_output=True)


def _text(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8")


def _lines(result: subprocess.CompletedProcess) -> list[str]:
    return sorted(_text(result).splitlines())


def _working_tree(root: Path) -> dict:
    entries = {}
    for folder, dirnames, filenames in os.walk(root):  # doesn't follow symlinks
        for name in dirnames + filenames:
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if relative == f"{PROJECT_DIR}/.git" or relative.startswith(f"{PROJECT_DIR}/.git/"):
                continue
            if path.is_symlink():
                entries[relative] = {"symlink": os.readlink(path)}
            elif path.is_dir():
                entries[relative] = {"directory": True}
            else:
                entries[relative] = _digest(path.read_bytes())
        dirnames[:] = [name for name in dirnames if not (Path(folder) / name).is_symlink()]
    return dict(sorted(entries.items()))


def _head_tree(project: Path) -> dict:
    tree = {}
    for entry in filter(None, _text(_git(project, "ls-tree", "-r", "-z", "--full-tree", "HEAD")).split("\0")):
        info, path = entry.split("\t", 1)
        mode, kind, object_id = info.split()
        assert kind == "blob", f"{path} is a {kind}"
        tree[path] = {"mode": mode, "sha256": hashlib.sha256(_git(project, "cat-file", "blob", object_id).stdout).hexdigest()}
    return tree


def _initial_commit(project: Path) -> dict | None:
    # HEAD after setup, which is the initial commit while "commits" is 1; None when setup made none.
    if _git(project, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
        return None
    header = _text(_git(project, "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B")).splitlines()
    return {
        "author": header[0],
        "committer": header[1],
        "message": "\n".join(header[2:]).strip(),
        "tree": _head_tree(project),
    }


def _refs(project: Path) -> list[str]:
    head = _git(project, "symbolic-ref", "-q", "HEAD").stdout.decode("utf-8").strip()
    return sorted(HEAD_BRANCH if ref == head else ref for ref in _lines(_git(project, "for-each-ref", "--format=%(refname)")))


def _git_state(project: Path) -> dict:
    commits = _git(project, "rev-list", "--count", "HEAD")
    hooks = project / ".git" / "hooks"
    return {
        "commits": int(commits.stdout) if commits.returncode == 0 else 0,
        "initial_commit": _initial_commit(project),
        "refs": _refs(project),
        "status": _lines(_git(project, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")),
        "untracked": _lines(_git(project, "ls-files", "--others")),
        "hooks": sorted(p.name for p in hooks.iterdir()) if hooks.is_dir() else [],
    }


def _setup_metadata(sample) -> dict:
    recorded = {"initial_commits": sample.metadata.get("initial_commits", 0)}
    if "outside_before" in sample.metadata:
        recorded["outside_before"] = {
            name: _digest(text.encode("utf-8")) for name, text in sorted(sample.metadata["outside_before"].items())
        }
    return recorded


async def _staged_sample(sample) -> dict:
    """Stage `sample` in Inspect's local sandbox, run its setup, and describe the result."""
    async with sandboxenv_context(
        task_name="equivalence",
        sandbox=SandboxEnvironmentSpec("local"),
        max_sandboxes=None,
        cleanup=True,
        sample=sample,
    ):
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


def differences(staged: dict, golden: dict) -> list[str]:
    """Where `staged` and `golden` differ: one line per scenario and section, naming the changed keys."""
    found = []
    for scenario in sorted(staged.keys() | golden.keys()):
        if scenario not in golden or scenario not in staged:
            found.append(f"{scenario}: {'not in the golden data' if scenario not in golden else 'no longer staged'}")
            continue
        for section in sorted(staged[scenario].keys() | golden[scenario].keys()):
            now, then = staged[scenario].get(section), golden[scenario].get(section)
            if now == then:
                continue
            if isinstance(now, dict) and isinstance(then, dict):
                changed = sorted(key for key in now.keys() | then.keys() if now.get(key) != then.get(key))
                found.append(f"{scenario}: {section}: {', '.join(changed)}")
            else:
                found.append(f"{scenario}: {section}")
    return found


def _to_json(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _read_golden(build, target: str, variant: str) -> dict:
    return json.loads(_golden_path(build, target, variant).read_text(encoding="utf-8"))


@pytest.mark.parametrize("build, target, variant", CASES, ids=lambda value: getattr(value, "__name__", value))
def test_todays_tasks_stage_what_the_golden_data_records(build, target, variant, home_with_blocking_git_hooks):
    found = differences(staged_projects(build, target, variant), _read_golden(build, target, variant))

    assert not found, (
        "The staged projects differ from tests/data/golden:\n" + "\n".join(found) + "\nIf that change is intended, "
        "re-record with `python tests/test_equivalence.py --record` and say why in the commit message."
    )


def test_differences_name_the_scenario_section_and_keys():
    golden = {"a": {"files": {"x": 1, "y": 2}, "git": {"commits": 1}}, "b": {"files": {}}}
    staged = {"a": {"files": {"x": 1, "y": 3, "z": 4}, "git": {"commits": 1}, "metadata": {}}, "c": {"files": {}}}
    assert differences(staged, golden) == [
        "a: files: y, z",
        "a: metadata",
        "b: no longer staged",
        "c: not in the golden data",
    ]
    assert differences(golden, golden) == []


def test_the_golden_data_covers_every_case_and_nothing_else():
    assert {path.name for path in GOLDEN_DIR.glob("*.json")} == {_golden_path(*case).name for case in CASES}


def test_kilo_jsonc_is_the_only_task_that_refuses_a_target():
    with pytest.raises(ValueError):
        kilo_jsonc(target="claude-code", model=MODEL)


@pytest.mark.parametrize("build", [agents_md, kilo_jsonc])
def test_the_golden_data_covers_every_shipped_scenario(build):
    for _, target, variant in [case for case in CASES if case[0] is build]:
        expected = sorted(str(sample.id) for sample in build(target=target, model=MODEL).dataset)
        assert sorted(_read_golden(build, target, variant)) == expected


@pytest.mark.parametrize("build", [agents_md, kilo_jsonc], ids=lambda build: build.__name__)
def test_the_cases_cover_every_variant_each_task_accepts(build, monkeypatch):
    allowed = []
    check = tasks_module._check_variant
    monkeypatch.setattr(tasks_module, "_check_variant", lambda variant, names: (allowed.append(set(names)), check(variant, names)))

    build(target="kilo", model=MODEL)

    assert allowed == [{variant for b, _, variant in CASES if b is build}]


def test_the_golden_data_is_written_as_the_recorder_writes_it():
    # Sorted keys and fixed indentation, so a re-recording diffs only where something changed.
    for path in GOLDEN_DIR.glob("*.json"):
        assert path.read_text(encoding="utf-8") == _to_json(json.loads(path.read_text(encoding="utf-8"))), path.name


def record() -> None:
    """Rewrite every golden file from today's tasks, under the test's hook-blocking HOME, printing what changed."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        for name, value in blocking_git_hooks_home(Path(scratch) / "user-home").items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for case in CASES:
            path = _golden_path(*case)
            staged = staged_projects(*case)
            previous = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            path.write_text(_to_json(staged), encoding="utf-8")
            changes = differences(staged, previous)
            print(f"{path.name}: {'unchanged' if not changes else 'changed'}")
            for line in changes:
                print(f"  {line}")


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        sys.exit("usage: python tests/test_equivalence.py --record   (rewrites tests/data/golden/; see the module docstring)")
    record()
