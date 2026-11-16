"""Behaviour the full mutation run on main (2026-11-16, Later plan Task 65) showed no test pinned down.

Each test names what it pins; the module it belongs to is in its name.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_checks_files import entry, evidence as files_evidence, folder
from test_checks_permissions_claude_code import PUSH, called, events, evidence as claude_evidence, judge, said
from test_trigger import add_copies, eval_file, project

from whitney import agent, cli, project_files
from whitney.checks import registry
from whitney.project_files import files_renamed, load_claude_code_project_files, load_project_files, not_staged_reason, staged_imports


def _write(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


@pytest.fixture
def case_blind_disk(monkeypatch):
    """A disk that spells CLAUDE.md as claude.md, as a case-insensitive one finds it (project_files._spelled)."""
    monkeypatch.setattr(project_files, "_spelled", lambda root, relative: relative.lower())


# --- project_files: renames on a case-insensitive disk ------------------------------------


def test_files_renamed_names_a_claude_code_instruction_file_spelled_otherwise_on_disk(tmp_path, case_blind_disk):
    _write(tmp_path, {"CLAUDE.md": "rule\n"})
    assert files_renamed("claude-code", tmp_path, {"CLAUDE.md": "rule\n"}) == {"claude.md": "CLAUDE.md"}


def test_files_renamed_is_empty_for_kilo_which_renames_nothing(tmp_path, case_blind_disk):
    _write(tmp_path, {"CLAUDE.md": "rule\n", "AGENTS.md": "rule\n"})
    assert files_renamed("kilo", tmp_path, {"CLAUDE.md": "rule\n", "AGENTS.md": "rule\n"}) == {}


def test_files_renamed_leaves_out_a_file_the_variant_didnt_stage(tmp_path, case_blind_disk):
    _write(tmp_path, {"CLAUDE.md": "rule\n"})
    assert files_renamed("claude-code", tmp_path, {}) == {}


def test_a_file_staged_under_its_disk_spelling_is_not_reported_as_left_out(tmp_path, case_blind_disk):
    _write(tmp_path, {"CLAUDE.md": "rule\n"})
    assert not_staged_reason("claude-code", tmp_path, "claude.md", {"CLAUDE.md": "rule\n"}) is None


# --- project_files: imports ------------------------------------------------------------------


def test_staged_imports_lists_each_file_once_and_passes_over_one_not_staged():
    staged = {
        "CLAUDE.md": "@a.md\n@b.md\n@missing.md\n",
        "a.md": "@c.md\n",
        "b.md": "@c.md\n@a.md\n",
        "c.md": "@CLAUDE.md\n",
    }
    assert staged_imports(["CLAUDE.md"], staged) == ["a.md", "b.md", "c.md"]


def test_an_import_that_cant_be_read_is_refused_with_the_reason(tmp_path, monkeypatch):
    """The reason as the OS gives it, whoever runs the tests (root can read a file chmod 0 makes unreadable)."""
    root = _write(tmp_path.resolve(), {"CLAUDE.md": "@locked.md\n", "locked.md": "r\n"})
    decoded = project_files._decoded

    def locked(path):
        if path.name == "locked.md":
            raise PermissionError(13, "Permission denied")
        return decoded(path)

    monkeypatch.setattr(project_files, "_decoded", locked)
    with pytest.raises(ValueError, match=r"imports @locked\.md, .*: it can't be read: Permission denied$"):
        load_claude_code_project_files(root)


# --- project_files: the evals folder is never looked in --------------------------------------


@pytest.mark.parametrize("pattern", ["**/docs/*/*.md", "docs/*/*.md"])
def test_an_instructions_pattern_never_lists_the_evals_folder_however_deep(tmp_path, monkeypatch, pattern):
    _write(tmp_path, {"kilo.jsonc": json.dumps({"instructions": [pattern]}), "docs/rules/a.md": "a\n", "docs/evals/b.md": "b\n"})
    evals = tmp_path / "docs" / "evals"
    listed = []
    iterdir = Path.iterdir

    def recording(self):
        listed.append(self.resolve())
        return iterdir(self)

    monkeypatch.setattr(Path, "iterdir", recording)
    staged = load_project_files(tmp_path, evals_path=evals)
    assert "docs/rules/a.md" in staged
    assert evals.resolve() not in listed


# --- cli -----------------------------------------------------------------------------------


def test_a_junit_path_that_is_a_folder_says_so(tmp_path):
    with pytest.raises(cli.CannotRun) as error:
        cli._clear_junit(str(tmp_path))
    assert str(error.value) == f"--junit {tmp_path} can't be written: {tmp_path} is a folder"


def test_a_docker_log_whose_eval_names_no_model_gives_the_model_given():
    log = SimpleNamespace(eval=SimpleNamespace(metadata={"execution": "docker"}, task_args={}))
    assert cli.report_model([log], "given/model") == "given/model"


def test_validate_leaves_out_a_skill_copy_under_an_evals_path_inside_the_project(project):
    """An evals root at .agents/ holds a copy of the skill; it is never staged, so no copy is left staged."""
    evals = project / ".agents"
    for name, text in {
        "skills/release-notes/SKILL.md": "---\nname: release-notes\ndescription: Use for release-notes.\n---\n",
        "fixtures/repo/project/README.md": "# App\n",
        "skills/release-notes.json": json.dumps(
            eval_file(variants={"no_skill": {"role": "baseline", "remove": [".kilo/skills/release-notes/"]}})
        ),
    }.items():
        (evals / name).parent.mkdir(parents=True, exist_ok=True)
        (evals / name).write_text(text)

    warnings = cli.validate(project, evals, target="kilo").warnings
    assert [w for w in warnings if "Kilo also reads the same skill" in w.message] == []


# --- checks.permissions ----------------------------------------------------------------------


def test_every_call_that_failed_without_being_refused_is_counted():
    stream = events("settings-deny-held")
    stream[-1:-1] = [
        called("toolu_fetch", "git push docs", tool="WebFetch"), said("toolu_fetch", "Request failed"),
        called("toolu_fetch2", "git push wiki", tool="WebFetch"), said("toolu_fetch2", "Request failed"),
    ]
    verdict = judge(claude_evidence(stream), PUSH)
    assert verdict.explanation.endswith("but 2 call(s) matching forbidden_input failed without being refused by this rule")


# --- checks.files ----------------------------------------------------------------------------


def test_a_string_in_a_file_the_run_added_counts_however_short():
    """A file that didn't exist had no text before, so one copy of any string is one more."""
    verdict = registry()["no_string_added"].run(files_evidence(folder(), folder(new=entry("XX"))), {"strings": ["XX"]})
    assert verdict.outcome == "fail"


# --- agent -----------------------------------------------------------------------------------


def test_a_run_without_an_overlay_or_a_named_agent_calls_the_runner_without_named(monkeypatch):
    seen = []

    async def runner(*args, **kwargs):
        seen.append(kwargs)
        return {}

    monkeypatch.setattr(agent, "_run_local", runner)
    asyncio.run(agent._run_with_overlay(None, None, "m/x", False, None, 10, None, None))
    assert seen == [{}]
