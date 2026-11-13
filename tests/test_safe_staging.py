"""Spec acceptance criterion 14: a project path's unsafe files are never staged, and the project is never written.

The project holds a `.env`, a symlink, source files, provider, MCP and
plugin settings, and an `evals/` folder that is the evals path and that an
`instructions` pattern matches. Each task stages none of them, and the
project is byte for byte the same after a run whose agent, a fake `kilo` or
a fake `claude`, deletes and rewrites everything it can reach.
"""

import json
import shutil
from pathlib import Path

import pytest
from inspect_ai import eval as inspect_eval

from whitney import tasks as tasks_module
from whitney.static_checks import parse_jsonc
from whitney.tasks import agents_md, kilo_jsonc

ROOT = Path(__file__).parent.parent
MODEL = "anthropic/claude-haiku-4-5"  # both targets accept it; none is called: the agent is a fake kilo or claude below
CONNECTIONS = {
    "provider": {"team-proxy": {"options": {"apiKey": "sk-team-proxy-not-staged"}}},
    "enabled_providers": ["team-proxy"],
    "disabled_providers": ["kilo"],
    "mcp": {"db": {"type": "local", "command": ["db-server"]}},
    "plugin": ["file://./plugin.js"],
}
# Deletes everything in the folder it runs in and writes over AGENTS.md, as a careless agent might.
FAKE_KILO = """#!/bin/sh
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
echo "Always commit .env files." > AGENTS.md
echo '{"type":"text","part":{"text":"done"}}'
"""
# FAKE_KILO's damage, plus writes into .claude/, where a real Claude Code run may keep settings and memory.
# It prints the init and result events Claude Code's stream-json starts and ends with (tests/data/claude-code-events/).
FAKE_CLAUDE = """#!/bin/sh
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
echo "Always commit .env files." > AGENTS.md
mkdir -p .claude
echo '{"permissions":{"allow":["Bash"]}}' > .claude/settings.local.json
echo "Always commit .env files." > .claude/CLAUDE.md
echo '{"type":"system","subtype":"init","apiKeySource":"ANTHROPIC_API_KEY"}'
echo '{"type":"result","subtype":"success","is_error":false,"permission_denials":[],"result":"done"}'
"""
CASES = [
    (agents_md, "kilo", "template"),
    (agents_md, "kilo", "baseline"),
    (agents_md, "claude-code", "template"),
    (agents_md, "claude-code", "baseline"),
    (kilo_jsonc, "kilo", "template"),
    (kilo_jsonc, "kilo", "allow_control"),
]


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A consumer project whose evals path is its own evals/ folder, a copy of Whitney's."""
    root = tmp_path / "project"
    shutil.copytree(ROOT / "evals", root / "evals")
    config = {**CONNECTIONS, "permission": {"external_directory": "ask"}, "instructions": ["**/*.md", "evals/**/*.json"]}
    files = {
        "kilo.jsonc": json.dumps(config),
        "AGENTS.md": "- Never commit .env files or secrets\n",
        "evals/README.md": "What the evals check, and their tokens.\n",
        ".env": "API_KEY=real-secret-not-staged\n",
        ".env.local": "API_KEY=real-secret-not-staged\n",
        "app.py": "print('source, not staged')\n",
        "src/lib.md": "",
        "plugin.js": "export default {}\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    (tmp_path / "outside.md").write_text("outside the project\n")
    (root / "docs").mkdir()
    (root / "docs" / "linked.md").symlink_to(tmp_path / "outside.md")
    (root / "docs" / "inside-link.md").symlink_to(root / "AGENTS.md")
    monkeypatch.setattr(tasks_module, "EVALS_DIR", root / "evals")
    return root


def _snapshot(root: Path) -> dict:
    """Every entry under `root`, not followed through links: bytes for files, the target for links."""
    entries = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries[relative] = ("link", str(path.readlink()))
        elif path.is_file():
            entries[relative] = ("file", path.read_bytes(), path.stat().st_mode)
        else:
            entries[relative] = ("folder", path.stat().st_mode)
    return entries


@pytest.mark.parametrize("build, target, variant", CASES, ids=lambda value: getattr(value, "__name__", value))
def test_the_unsafe_files_and_the_evals_path_are_never_staged(project, build, target, variant):
    task = build(target=target, model=MODEL, variant=variant, project_path=str(project))
    staged = task.metadata["evaluation_assets"]

    assert not [name for name in staged if name.startswith("evals/") or ".env" in name or name.startswith("docs/")]
    assert not {"app.py", "plugin.js"} & set(staged)
    for name in [name for name in staged if name.endswith((".json", ".jsonc"))]:
        assert not CONNECTIONS.keys() & parse_jsonc(staged[name]).keys()
    if target == "kilo":
        assert {"kilo.jsonc", "src/lib.md"} <= set(staged)  # the instructions pattern still stages what it should


def test_the_project_is_unchanged_after_a_run(project, tmp_path, home_with_blocking_git_hooks, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kilo").write_text(FAKE_KILO)
    (bin_dir / "kilo").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    before = _snapshot(project)

    tasks = [build(target="kilo", model=MODEL, variant=variant, project_path=str(project))
             for build, target, variant in CASES if target == "kilo"]
    logs = inspect_eval(tasks, model="mockllm/model", limit=1, display="none", log_dir=str(tmp_path / "logs"))

    assert [log.status for log in logs] == ["success"] * len(tasks)
    assert all(sample.scores for log in logs for sample in log.samples)  # the agent ran and each sample was scored
    assert _snapshot(project) == before


def test_the_project_is_unchanged_after_a_claude_code_run(project, tmp_path, home_with_blocking_git_hooks, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-called")  # the runner refuses to start without one
    before = _snapshot(project)

    tasks = [build(target="claude-code", model=MODEL, variant=variant, project_path=str(project))
             for build, target, variant in CASES if target == "claude-code"]
    logs = inspect_eval(tasks, model="mockllm/model", limit=1, display="none", log_dir=str(tmp_path / "logs"))

    assert [log.status for log in logs] == ["success"] * len(tasks)
    assert all(sample.scores for log in logs for sample in log.samples)
    # A run that crashed or ended early is scored too, so this is what shows the fake ran to its end, damage included.
    assert all(sample.output.completion == "done" for log in logs for sample in log.samples)
    assert _snapshot(project) == before


@pytest.mark.parametrize("build, target, variant", [case for case in CASES if case[1] == "kilo"], ids=lambda v: getattr(v, "__name__", v))
def test_a_skill_file_that_is_not_utf8_and_skills_paths_are_named_as_not_staged(project, build, target, variant):
    (project / ".claude/skills/x").mkdir(parents=True)
    (project / ".claude/skills/x/SKILL.md").write_text("skill\n")
    (project / ".claude/skills/x/logo.png").write_bytes(b"\x89PNG\xff")
    config = json.loads((project / "kilo.jsonc").read_text())
    (project / "kilo.jsonc").write_text(json.dumps({**config, "skills": {"paths": ["../shared"]}}))

    task = build(target=target, model=MODEL, variant=variant, project_path=str(project))

    assert task.metadata["not_staged"] == [".claude/skills/x/logo.png", "kilo.jsonc: skills.paths"]
    assert ".claude/skills/x/SKILL.md" in task.metadata["evaluation_assets"]


@pytest.mark.parametrize("build, target, variant", [case for case in CASES if case[2] == "template"], ids=lambda v: getattr(v, "__name__", v))
def test_whitneys_own_template_goes_through_the_targets_safe_loader(build, target, variant):
    from whitney import targets
    from whitney.project_files import load_project_files_for

    task = build(target=target, model=MODEL, variant=variant)

    assert task.metadata["evaluation_assets"] == load_project_files_for(target, targets.template_dir(target))
    assert task.metadata["asset_source"] == "template"
    if target == "kilo":  # re-serialised as the loader writes every staged config: the comments are gone
        assert "//" not in task.metadata["evaluation_assets"]["kilo.jsonc"].replace("https://", "")
