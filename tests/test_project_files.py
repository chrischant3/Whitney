import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from whitney import project_files
from whitney.project_files import (
    files_not_staged,
    files_not_staged_reasons,
    files_renamed,
    load_claude_code_project_files,
    load_project_files,
    load_project_files_for,
    load_template_files,
    not_staged_reason,
    set_external_directory,
)
from whitney.static_checks import parse_jsonc


def test_template_files_include_nested_text_but_skip_secret_files(tmp_path):
    (tmp_path / "AGENTS.md").write_text("keep rules\n")
    (tmp_path / ".kilo/skills/review").mkdir(parents=True)
    (tmp_path / ".kilo/skills/review/SKILL.md").write_text("review skill\n")
    (tmp_path / ".env").write_text("real-secret\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("repo metadata\n")

    assert load_template_files(tmp_path) == {
        "AGENTS.md": "keep rules\n",
        ".kilo/skills/review/SKILL.md": "review skill\n",
    }


def test_project_files_copy_policy_and_instruction_assets_not_source_code(tmp_path):
    (tmp_path / ".kilo/skills/audit").mkdir(parents=True)
    (tmp_path / "kilo.jsonc").write_text(
        '{"provider":{"my-proxy":{"options":{"apiKey":"not-copied"}}},'
        '"instructions":["team-rules.md"],"permission":{"external_directory":"ask"}}'
    )
    (tmp_path / "AGENTS.md").write_text("project rule\n")
    (tmp_path / "team-rules.md").write_text("team rule\n")
    (tmp_path / ".kilo/skills/audit/SKILL.md").write_text("audit skill\n")
    (tmp_path / "app.py").write_text("project source\n")
    (tmp_path / ".env").write_text("real-secret\n")

    files = load_project_files(tmp_path)
    config = parse_jsonc(files["kilo.jsonc"])

    assert set(files) == {"AGENTS.md", "team-rules.md", ".kilo/skills/audit/SKILL.md", "kilo.jsonc"}
    assert config["permission"]["external_directory"] == "ask"
    assert "provider" not in config


def test_project_allow_control_changes_copied_config_only(tmp_path):
    (tmp_path / "kilo.jsonc").write_text('{"permission":{"external_directory":"ask"}}')
    original = {"kilo.jsonc": (tmp_path / "kilo.jsonc").read_text()}

    controlled = set_external_directory(original, "allow")

    assert parse_jsonc(controlled["kilo.jsonc"])["permission"]["external_directory"] == "allow"
    assert parse_jsonc(original["kilo.jsonc"])["permission"]["external_directory"] == "ask"


def test_project_path_requires_kilo_config(tmp_path):
    with pytest.raises(ValueError, match="no Kilo config"):
        load_project_files(tmp_path)


@pytest.mark.parametrize("load", [load_template_files, load_project_files])
def test_a_missing_folder_is_not_found_and_a_file_is_not_a_folder(tmp_path, load):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "missing")
    (tmp_path / "file.txt").write_text("x")
    kind = "template" if load is load_template_files else "project"
    with pytest.raises(ValueError, match=f"^{kind} path is not a directory: {tmp_path / 'file.txt'}$"):
        load(tmp_path / "file.txt")


def test_a_project_without_kilo_config_is_told_where_it_could_be(tmp_path):
    with pytest.raises(ValueError) as raised:
        load_project_files(tmp_path)
    assert str(raised.value) == (
        "project has no Kilo config (kilo.jsonc, kilo.json, .kilo/kilo.jsonc, .kilo/kilo.json, "
        f".kilocode/kilo.jsonc, .kilocode/kilo.json): {tmp_path.resolve()}"
    )


def test_the_staged_config_is_the_projects_config_without_connections_written_as_indented_json(tmp_path):
    (tmp_path / "kilo.jsonc").write_text('{"provider": {"x": {}}, "permission": {"bash": "ask"}}  // comment')
    assert load_project_files(tmp_path)["kilo.jsonc"] == '{\n  "permission": {\n    "bash": "ask"\n  }\n}'


def test_instructions_can_be_a_single_pattern_or_absent(tmp_path):
    (tmp_path / "rules.md").write_text("one rule\n")
    (tmp_path / "kilo.jsonc").write_text('{"instructions": "rules.md"}')
    assert load_project_files(tmp_path)["rules.md"] == "one rule\n"

    (tmp_path / "kilo.jsonc").write_text("{}")
    assert set(load_project_files(tmp_path)) == {"kilo.jsonc"}


@pytest.mark.parametrize("pattern", ["/etc/hosts", "../outside.md", "docs/../../outside.md"])
def test_an_instruction_pattern_must_stay_inside_the_project(tmp_path, pattern):
    (tmp_path / "kilo.jsonc").write_text(f'{{"instructions": ["{pattern}"]}}')
    with pytest.raises(ValueError, match=f"^project instruction path must stay inside the project: '{pattern}'$"):
        load_project_files(tmp_path)


def test_instruction_matches_skip_folders_symlinks_and_secret_files(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "a.md").write_text("rule a\n")
    (tmp_path / "rules" / "folder.md").mkdir()
    (tmp_path / "rules" / "linked.md").symlink_to(tmp_path / "rules" / "a.md")
    (tmp_path / "rules" / ".env.production").write_text("SECRET=x\n")
    (tmp_path / "kilo.jsonc").write_text('{"instructions": ["rules/*"]}')

    files = load_project_files(tmp_path)

    assert {name: text for name, text in files.items() if name.startswith("rules/")} == {"rules/a.md": "rule a\n"}


def test_env_variants_are_left_out_of_templates_but_examples_are_kept(tmp_path):
    for name in (".env.local", ".env.production", ".env.example"):
        (tmp_path / name).write_text("X=1\n")
    assert set(load_template_files(tmp_path)) == {".env.example"}


def test_a_file_that_is_not_utf8_is_named(tmp_path):
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe rules")
    with pytest.raises(ValueError, match=f"^template/config asset must be UTF-8 text: {tmp_path.resolve() / 'AGENTS.md'}$"):
        load_template_files(tmp_path)


def test_setting_external_directory_adds_a_permission_block_when_there_is_none(tmp_path):
    controlled = set_external_directory({"kilo.json": '{"model": "m"}'}, "deny")
    assert controlled["kilo.json"] == '{\n  "model": "m",\n  "permission": {\n    "external_directory": "deny"\n  }\n}'


def test_setting_external_directory_needs_a_kilo_config():
    with pytest.raises(ValueError, match="^no Kilo config file found in project assets$"):
        set_external_directory({"AGENTS.md": "x"}, "allow")


def test_assets_are_read_as_utf8_whatever_the_locale(tmp_path, ascii_locale):
    (tmp_path / "AGENTS.md").write_text("- Règle : jamais de secrets\n", encoding="utf-8")
    assert load_template_files(tmp_path) == {"AGENTS.md": "- Règle : jamais de secrets\n"}


# The claude-code target's loader. It stages one instruction file, always as AGENTS.md: root
# AGENTS.md, else root CLAUDE.md, else .claude/CLAUDE.md (tasks/plan-phase5.md, decision 5, as
# revised 2026-11-06). Nothing else is staged.


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"])
def test_a_claude_code_project_with_one_instruction_file_stages_it_without_kilo_config(tmp_path, name):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("- Team rule\n")
    assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "- Team rule\n"}


def test_a_claude_code_project_stages_only_its_instruction_files(tmp_path):
    # The 2026-11-05 reproduction: AGENTS.md and a skill under .claude/, no Kilo config.
    (tmp_path / ".claude/skills/demo").mkdir(parents=True)
    (tmp_path / ".claude/skills/demo/SKILL.md").write_text("demo skill\n")
    (tmp_path / ".claude/settings.json").write_text('{"hooks": {}}')
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    (tmp_path / "AGENTS.md").write_text("agents rule\n")
    (tmp_path / "CLAUDE.md").write_text("claude rule\n")
    (tmp_path / "CONTEXT.md").write_text("kilo context\n")
    (tmp_path / "kilo.jsonc").write_text("{}")
    (tmp_path / "app.py").write_text("project source\n")
    (tmp_path / ".env").write_text("SECRET=x\n")
    (tmp_path / ".claude/.env").write_text("SECRET=x\n")

    assert load_claude_code_project_files(tmp_path) == {
        "AGENTS.md": "agents rule\n",
    }


def test_a_claude_code_project_without_instructions_is_told_which_files_were_looked_for(tmp_path):
    (tmp_path / "kilo.jsonc").write_text("{}")
    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(tmp_path)
    assert str(raised.value) == (
        "project has no Claude Code instructions (AGENTS.md, CLAUDE.md, .claude/CLAUDE.md; "
        f"symlinks and files outside the project are skipped): {tmp_path.resolve()}"
    )


def test_claude_code_instruction_files_that_are_symlinks_are_not_staged(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "outside.md").write_text("outside rule\n")
    (project / "real.md").write_text("inside rule\n")
    (project / "AGENTS.md").symlink_to(project / "real.md")
    (project / "CLAUDE.md").symlink_to(tmp_path / "outside.md")
    (project / ".claude").mkdir()
    (project / ".claude/CLAUDE.md").write_text("folder rule\n")

    assert load_claude_code_project_files(project) == {"AGENTS.md": "folder rule\n"}


def test_a_dot_claude_folder_that_links_outside_the_project_stages_nothing(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/CLAUDE.md").write_text("outside rule\n")
    (project / ".claude").symlink_to(tmp_path / "elsewhere")

    with pytest.raises(ValueError, match="^project has no Claude Code instructions"):
        load_claude_code_project_files(project)


def test_a_claude_code_instruction_file_that_is_not_utf8_is_named(tmp_path):
    (tmp_path / "CLAUDE.md").write_bytes(b"\xff\xfe rules")
    with pytest.raises(ValueError, match=f"^template/config asset must be UTF-8 text: {tmp_path.resolve() / 'CLAUDE.md'}$"):
        load_claude_code_project_files(tmp_path)


def test_a_missing_claude_code_project_is_not_found_and_a_file_is_not_a_folder(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_claude_code_project_files(tmp_path / "missing")
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(ValueError, match=f"^project path is not a directory: {tmp_path / 'file.txt'}$"):
        load_claude_code_project_files(tmp_path / "file.txt")


def test_the_loader_is_picked_by_target(tmp_path):
    (tmp_path / "AGENTS.md").write_text("- Team rule\n")
    assert load_project_files_for("claude-code", tmp_path) == {"AGENTS.md": "- Team rule\n"}
    with pytest.raises(ValueError, match="^project has no Kilo config"):
        load_project_files_for("kilo", tmp_path)
    (tmp_path / "kilo.jsonc").write_text("{}")
    assert load_project_files_for("kilo", tmp_path) == load_project_files(tmp_path)


def test_the_dot_claude_entries_left_out_are_named_for_the_report(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude/skills/demo").mkdir(parents=True)
    (tmp_path / ".claude/skills/demo/SKILL.md").write_text("demo skill\n")
    (tmp_path / ".claude/settings.json").write_text('{"hooks": {}}')
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")

    assert files_not_staged("claude-code", tmp_path) == [".claude/CLAUDE.md", ".claude/settings.json", ".claude/skills/"]
    assert files_not_staged("kilo", tmp_path) == []


def test_nothing_is_named_without_a_dot_claude_folder_and_a_dot_claude_md_beside_agents_md_is_named(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    assert files_not_staged("claude-code", tmp_path) == []
    (tmp_path / ".claude").write_text("a file, not a folder")
    assert files_not_staged("claude-code", tmp_path) == []
    (tmp_path / ".claude").unlink()
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    assert files_not_staged("claude-code", tmp_path) == [".claude/CLAUDE.md"]


def test_a_dot_claude_md_that_was_not_staged_is_named(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").symlink_to(tmp_path / "AGENTS.md")
    assert files_not_staged("claude-code", tmp_path) == [".claude/CLAUDE.md"]


def test_a_dot_claude_folder_outside_the_project_is_named_but_not_listed(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rule\n")
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere/private-notes.md").write_text("not the project's\n")
    (project / ".claude").symlink_to(tmp_path / "elsewhere")
    assert files_not_staged("claude-code", project) == [".claude/"]


def test_a_symlinked_dot_claude_folder_stages_nothing_through_it_and_is_named_whole(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("root rule\n")
    (tmp_path / ".claude").symlink_to(tmp_path)  # inside the project, but still a symlink

    assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "root rule\n"}
    assert files_not_staged("claude-code", tmp_path) == [".claude/"]


def test_a_symlinked_entry_in_dot_claude_is_named_without_following_it(tmp_path):
    (tmp_path / "outside").mkdir()
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/CLAUDE.md").write_text("folder rule\n")
    (project / ".claude/shared").symlink_to(tmp_path / "outside")
    (project / ".claude/alias.md").symlink_to(project / ".claude/CLAUDE.md")
    (project / ".claude/agents").mkdir()  # a real folder gets its "/"; the linked one doesn't

    assert files_not_staged("claude-code", project) == [".claude/agents/", ".claude/alias.md", ".claude/shared"]


def test_a_dot_claude_md_staged_under_another_case_is_not_named(tmp_path):
    (tmp_path / "probe").write_text("")
    if not (tmp_path / "PROBE").exists():
        pytest.skip("needs a case-insensitive filesystem, such as macOS's default")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/claude.md").write_text("folder rule\n")

    assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "folder rule\n"}
    assert files_not_staged("claude-code", tmp_path) == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a folder whatever its mode")
def test_an_unreadable_dot_claude_folder_is_named_whole(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text("{}")
    (tmp_path / ".claude").chmod(0o000)
    try:
        assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "rule\n"}
        assert files_not_staged("claude-code", tmp_path) == [".claude/"]
    finally:
        (tmp_path / ".claude").chmod(0o755)


def test_root_instruction_files_skipped_for_safety_are_named(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/CLAUDE.md").write_text("shared rule\n")
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/CLAUDE.md").write_text("folder rule\n")
    (project / "CLAUDE.md").symlink_to(tmp_path / "shared/CLAUDE.md")
    (project / "AGENTS.md").symlink_to(project / "missing.md")  # a broken link is named too

    assert load_claude_code_project_files(project) == {"AGENTS.md": "folder rule\n"}
    assert files_not_staged("claude-code", project) == ["AGENTS.md", "CLAUDE.md"]


@pytest.mark.parametrize("count, expected_tail", [(50, ".claude/f49.md"), (52, "and 2 more")])
def test_the_not_staged_list_stops_at_fifty_names_and_counts_the_rest(tmp_path, count, expected_tail):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude").mkdir()
    for number in range(count):
        (tmp_path / f".claude/f{number:02d}.md").write_text("x\n")

    names = files_not_staged("claude-code", tmp_path)

    assert names[:50] == [f".claude/f{number:02d}.md" for number in range(50)]
    assert names[-1] == expected_tail
    assert len(names) == min(count, 51)


def test_a_dot_claude_md_that_cannot_be_checked_is_not_staged_and_the_folder_is_named(tmp_path, monkeypatch):
    # Python 3.11 and 3.12 raise PermissionError for a path inside a mode-000 folder; 3.14 returns False.
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    real_is_file, real_iterdir = Path.is_file, Path.iterdir

    def is_file(path):
        if path.parent.name == ".claude":
            raise PermissionError(13, "Permission denied", str(path))
        return real_is_file(path)

    def iterdir(path):
        if path.name == ".claude":
            raise PermissionError(13, "Permission denied", str(path))
        return real_iterdir(path)

    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "iterdir", iterdir)

    assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "rule\n"}
    assert files_not_staged("claude-code", tmp_path) == [".claude/"]


def test_the_cap_never_hides_a_skipped_root_file(tmp_path):
    (tmp_path / "shared.md").write_text("shared rule\n")
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / "AGENTS.md").write_text("rule\n")
    (project / "CLAUDE.md").symlink_to(tmp_path / "shared.md")
    for number in range(60):
        (project / f".claude/f{number:02d}.md").write_text("x\n")

    names = files_not_staged("claude-code", project)

    assert names[0] == "CLAUDE.md"
    assert names[1:] == [*(f".claude/f{number:02d}.md" for number in range(50)), "and 10 more"]


@pytest.mark.parametrize("name", ["CLAUDE.md", ".claude/CLAUDE.md"])
def test_a_claude_md_beside_agents_md_is_named_not_staged_and_agents_md_is_staged(tmp_path, name):
    # Claude Code ignored AGENTS.md once .claude/CLAUDE.md existed (docs/results/2026-11-06-claude-code-canary.md).
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("claude rule\n")
    (tmp_path / "AGENTS.md").write_text("agents rule\n")

    assert load_claude_code_project_files(tmp_path) == {"AGENTS.md": "agents rule\n"}
    assert files_not_staged("claude-code", tmp_path) == [name]
    assert files_renamed("claude-code", tmp_path, {"AGENTS.md": "agents rule\n"}) == {}


@pytest.mark.parametrize("name", ["CLAUDE.md", ".claude/CLAUDE.md"])
def test_a_claude_md_with_no_agents_md_beside_it_is_staged_as_agents_md_and_the_rename_is_named(tmp_path, name):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("claude rule\n")
    staged = load_claude_code_project_files(tmp_path)

    assert staged == {"AGENTS.md": "claude rule\n"}
    assert files_renamed("claude-code", tmp_path, staged) == {name: "AGENTS.md"}
    assert files_not_staged("claude-code", tmp_path) == []
    assert (tmp_path / name).read_text() == "claude rule\n"  # the project itself is never changed
    assert not (tmp_path / "AGENTS.md").exists()


def test_with_both_claude_md_files_and_no_agents_md_the_root_one_is_staged(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    (tmp_path / "CLAUDE.md").write_text("root rule\n")
    staged = load_claude_code_project_files(tmp_path)

    assert staged == {"AGENTS.md": "root rule\n"}
    assert files_renamed("claude-code", tmp_path, staged) == {"CLAUDE.md": "AGENTS.md"}
    assert files_not_staged("claude-code", tmp_path) == [".claude/CLAUDE.md"]


def test_an_agents_md_skipped_for_safety_gives_way_to_claude_md_and_is_named(tmp_path):
    (tmp_path / "shared.md").write_text("shared rule\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").symlink_to(tmp_path / "shared.md")
    (project / "CLAUDE.md").write_text("root rule\n")

    assert load_claude_code_project_files(project) == {"AGENTS.md": "root rule\n"}
    assert files_not_staged("claude-code", project) == ["AGENTS.md"]


def test_a_rename_is_named_only_while_the_renamed_file_is_staged(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("root rule\n")
    assert files_renamed("claude-code", tmp_path, {}) == {}  # the baseline drops AGENTS.md
    assert files_renamed("kilo", tmp_path, {"AGENTS.md": "x"}) == {}


def test_the_cap_never_hides_a_dot_claude_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / "CLAUDE.md").write_text("root rule\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    for number in range(60):
        (tmp_path / f".claude/0{number:02d}.md").write_text("x\n")  # digits sort ahead of "CLAUDE.md"

    names = files_not_staged("claude-code", tmp_path)

    assert names[:2] == ["CLAUDE.md", ".claude/CLAUDE.md"]
    assert names[2:] == [*(f".claude/0{number:02d}.md" for number in range(50)), "and 10 more"]


def test_a_dot_claude_md_reached_through_a_symlinked_dot_claude_folder_is_not_staged(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared/CLAUDE.md").write_text("folder rule\n")
    (tmp_path / ".claude").symlink_to(tmp_path / "shared")  # inside the project, but still a symlink

    with pytest.raises(ValueError, match="^project has no Claude Code instructions"):
        load_claude_code_project_files(tmp_path)
    assert files_not_staged("claude-code", tmp_path) == [".claude/"]


def test_the_dot_claude_entries_are_named_even_when_nothing_could_be_staged(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text("{}")
    assert files_not_staged("claude-code", tmp_path) == [".claude/settings.json"]


def test_a_dot_claude_md_that_cannot_be_checked_is_never_staged_as_agents_md(tmp_path, monkeypatch):
    # With no root file to stage, .claude/CLAUDE.md is checked; 3.11 and 3.12 raise inside a mode-000 folder.
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    real_is_file = Path.is_file

    def is_file(path):
        if path.parent.name == ".claude":
            raise PermissionError(13, "Permission denied", str(path))
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file)

    with pytest.raises(ValueError, match="^project has no Claude Code instructions"):
        load_claude_code_project_files(tmp_path)


def test_each_name_left_out_has_its_reason(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / "CLAUDE.md").write_text("root rule\n")
    (tmp_path / ".claude/agents").mkdir(parents=True)
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    (tmp_path / ".claude/settings.json").write_text("{}")

    assert files_not_staged_reasons("claude-code", tmp_path) == {
        "CLAUDE.md": "beside AGENTS.md",
        ".claude/CLAUDE.md": "beside AGENTS.md",
        ".claude/agents/": "rest of .claude/",
        ".claude/settings.json": "rest of .claude/",
    }
    assert files_not_staged_reasons("kilo", tmp_path) == {}


def test_an_instruction_file_skipped_for_safety_and_one_beside_a_staged_claude_md_have_their_reasons(tmp_path):
    (tmp_path / "shared.md").write_text("shared rule\n")
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / "AGENTS.md").symlink_to(tmp_path / "shared.md")
    (project / "CLAUDE.md").write_text("root rule\n")
    (project / ".claude/CLAUDE.md").write_text("folder rule\n")

    assert files_not_staged_reasons("claude-code", project) == {
        "AGENTS.md": "a symlink or outside the project",
        ".claude/CLAUDE.md": "beside CLAUDE.md",
    }


def test_a_dot_claude_md_that_is_a_symlink_has_the_safety_reason(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").symlink_to(tmp_path / "AGENTS.md")
    assert files_not_staged_reasons("claude-code", tmp_path) == {".claude/CLAUDE.md": "a symlink or outside the project"}


def test_a_dot_claude_folder_named_whole_has_its_reason_and_the_count_has_none(tmp_path):
    (tmp_path / "elsewhere").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("rule\n")
    (project / ".claude").symlink_to(tmp_path / "elsewhere")
    assert files_not_staged_reasons("claude-code", project) == {".claude/": "a symlink, or can't be read"}

    (project / ".claude").unlink()
    (project / ".claude").mkdir()
    for number in range(52):
        (project / f".claude/f{number:02d}.md").write_text("x\n")
    reasons = files_not_staged_reasons("claude-code", project)
    assert list(reasons) == files_not_staged("claude-code", project)[:50]


def test_an_entry_in_dot_claude_that_cannot_be_checked_names_the_folder_whole(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude/agents").mkdir(parents=True)
    real_is_dir = Path.is_dir

    def is_dir(path):
        if path.parent.name == ".claude":
            raise PermissionError(13, "Permission denied", str(path))
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    assert files_not_staged("claude-code", tmp_path) == [".claude/"]


@pytest.fixture
def case_insensitive(tmp_path):
    (tmp_path / "probe").write_text("")
    found = (tmp_path / "PROBE").exists()
    (tmp_path / "probe").unlink()
    return found


@pytest.mark.parametrize("name", ["claude.md", "agents.md"])
def test_names_are_matched_as_the_filesystem_does_and_reported_as_spelled_on_disk(tmp_path, case_insensitive, name):
    (tmp_path / name).write_text("rule\n")
    if not case_insensitive:  # Linux: neither name is an instruction file
        with pytest.raises(ValueError, match="^project has no Claude Code instructions"):
            load_claude_code_project_files(tmp_path)
        return
    staged = load_claude_code_project_files(tmp_path)
    assert staged == {"AGENTS.md": "rule\n"}
    assert files_renamed("claude-code", tmp_path, staged) == {name: "AGENTS.md"}


def test_a_root_claude_md_spelled_otherwise_is_named_as_spelled_on_disk(tmp_path, case_insensitive):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / "Claude.md").write_text("root rule\n")
    expected = {"Claude.md": "beside AGENTS.md"} if case_insensitive else {}
    assert files_not_staged_reasons("claude-code", tmp_path) == expected


def test_a_case_variant_dot_claude_md_stays_ahead_of_the_cap(tmp_path, case_insensitive):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/claude.md").write_text("folder rule\n")
    for number in range(60):
        (tmp_path / f".claude/0{number:02d}.md").write_text("x\n")

    names = files_not_staged("claude-code", tmp_path)

    assert names[0] == ".claude/claude.md"
    expected = "beside AGENTS.md" if case_insensitive else "rest of .claude/"  # on Linux it isn't Claude Code's file
    assert files_not_staged_reasons("claude-code", tmp_path)[".claude/claude.md"] == expected


def test_a_dot_claude_md_in_a_folder_that_cannot_be_listed_is_renamed_under_its_looked_up_name(tmp_path, monkeypatch):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    real_iterdir = Path.iterdir

    def iterdir(path):  # a folder with search but not read permission: its files open, it can't be listed
        if path.name == ".claude":
            raise PermissionError(13, "Permission denied", str(path))
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    staged = load_claude_code_project_files(tmp_path)

    assert files_renamed("claude-code", tmp_path, staged) == {".claude/CLAUDE.md": "AGENTS.md"}
    assert files_not_staged("claude-code", tmp_path) == [".claude/"]


# Phase 5 Task 8: the Kilo loader stages what Kilo 7.8.18 reads without being told to, checked
# with `kilo debug config --pure` and `kilo debug skill --pure` (docs/load-order.md, "Rules" and
# "Skills, agents and commands"), under the same safety rules as everything else it stages.


def _kilo_project(root, files):
    (root / "kilo.jsonc").write_text("{}")
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def test_rules_kilo_reads_without_an_instructions_entry_are_staged(tmp_path):
    _kilo_project(tmp_path, {
        ".kilo/rules/a.md": "a\n",
        ".kilocode/rules/b.md": "b\n",
        ".kilocode/rules/.hidden.md": "hidden\n",
        ".kilo/rules-code/c.md": "c\n",
        ".kilocode/rules-orchestrator/d.md": "d\n",
        ".kilocoderules": "legacy\n",
        ".kilocoderules-architect": "legacy architect\n",
    })

    files = load_project_files(tmp_path)

    assert set(files) == {
        "kilo.jsonc", ".kilo/rules/a.md", ".kilocode/rules/b.md", ".kilocode/rules/.hidden.md",
        ".kilo/rules-code/c.md", ".kilocode/rules-orchestrator/d.md", ".kilocoderules", ".kilocoderules-architect",
    }
    assert files[".kilocode/rules/b.md"] == "b\n"


def test_only_the_rules_kilo_reads_are_staged_and_never_an_unsafe_one(tmp_path):
    # Kilo reads the .md files directly in a rules folder, with a lowercase suffix, and only for its five modes.
    _kilo_project(tmp_path, {
        ".kilocode/rules/nested/deep.md": "nested\n",
        ".kilocode/rules/notes.txt": "not markdown\n",
        ".kilocode/rules/UPPER.MD": "upper case suffix\n",
        ".kilocode/rules-review/r.md": "not one of Kilo's modes\n",
        ".kilocode/rules/.env.md": "SECRET=x\n",
        ".kilocoderules-review": "not one of Kilo's modes\n",
        "elsewhere/real.md": "linked to\n",
    })
    (tmp_path / ".kilocode/rules/folder.md").mkdir()
    (tmp_path / ".kilocode/rules/linked.md").symlink_to(tmp_path / "elsewhere/real.md")
    (tmp_path / ".kilocoderules").symlink_to(tmp_path / "elsewhere/real.md")

    assert set(load_project_files(tmp_path)) == {"kilo.jsonc"}


def test_skills_kilo_reads_from_dot_claude_and_dot_agents_are_staged_and_nothing_else_there(tmp_path):
    _kilo_project(tmp_path, {
        ".claude/skills/review/SKILL.md": "review skill\n",
        ".claude/skills/review/scripts/check.sh": "echo check\n",
        ".agents/skills/release/SKILL.md": "release skill\n",
        ".claude/skills/review/.env": "SECRET=x\n",
        ".claude/settings.json": '{"hooks": {}}',
        ".claude/agents/helper.md": "not read by Kilo\n",
        ".claude/CLAUDE.md": "not read by Kilo from .claude/\n",
        ".agents/notes.md": "not a skill\n",
    })

    assert set(load_project_files(tmp_path)) == {
        "kilo.jsonc", ".claude/skills/review/SKILL.md", ".claude/skills/review/scripts/check.sh", ".agents/skills/release/SKILL.md",
    }


@pytest.mark.parametrize("linked", [".claude", ".agents/skills", ".kilocode", ".kilocode/rules", ".kilo/skills"])
def test_a_folder_reached_through_a_symlink_stages_nothing_even_inside_the_project(tmp_path, linked):
    _kilo_project(tmp_path, {
        "real/skills/x/SKILL.md": "skill\n", "real/x/SKILL.md": "skill\n", "real/rules/r.md": "rule\n", "real/r.md": "rule\n",
    })
    (tmp_path / linked).parent.mkdir(parents=True, exist_ok=True)
    target = {".claude": "real", ".kilocode": "real", ".agents/skills": "real", ".kilo/skills": "real", ".kilocode/rules": "real"}[linked]
    (tmp_path / linked).symlink_to(tmp_path / target)

    assert not [name for name in load_project_files(tmp_path) if name.startswith(linked.split("/")[0])]


# `instructions` patterns are expanded as Kilo 7.8.18 expands them: node-glob with dot files
# matched, symlinked folders not followed, files only, and names compared without regard to case
# on macOS and Windows (docs/load-order.md, "Instructions globs"). Each expected set below is what
# that expansion gave for the same tree.
GLOB_TREE = (
    "top.md", "docs/d.md", ".hidden/h.md", "rules/a.md", "rules/.dot.md", "rules/x.txt", "rules/sub/b.md", "rules/sub/deep/c.md",
)
CASE_BLIND = sys.platform in ("darwin", "win32")


def _glob_project(root, pattern):
    _kilo_project(root, {name: f"{name}\n" for name in GLOB_TREE})
    (root / "kilo.jsonc").write_text(json.dumps({"instructions": [pattern]}))
    return {name for name in load_project_files(root) if name != "kilo.jsonc"}


@pytest.mark.parametrize("pattern, expected", [
    ("rules/*.md", {"rules/a.md", "rules/.dot.md"}),
    ("rules/**/*.md", {"rules/a.md", "rules/.dot.md", "rules/sub/b.md", "rules/sub/deep/c.md"}),
    ("**/*.md", {"top.md", "docs/d.md", ".hidden/h.md", "rules/a.md", "rules/.dot.md", "rules/sub/b.md", "rules/sub/deep/c.md"}),
    ("**", set(GLOB_TREE)),
    ("rules/**", {"rules/a.md", "rules/.dot.md", "rules/x.txt", "rules/sub/b.md", "rules/sub/deep/c.md"}),
    ("rules/sub/**", {"rules/sub/b.md", "rules/sub/deep/c.md"}),
    ("rules/*", {"rules/a.md", "rules/.dot.md", "rules/x.txt"}),
    ("rules", set()),
    ("*.md", {"top.md"}),
    ("top.md", {"top.md"}),
    ("rules/sub/b.md", {"rules/sub/b.md"}),
    ("./top.md", {"top.md"}),
    ("rules//a.md", {"rules/a.md"}),
    ("rules/?.md", {"rules/a.md"}),
    ("rules/[a-c].md", {"rules/a.md"}),
    ("rules/[!a].md", set()),
    ("rules/{a,x}.*", {"rules/a.md", "rules/x.txt"}),
    ("{top,rules/{a,sub/b}}.md", {"top.md", "rules/a.md", "rules/sub/b.md"}),
    ("rules/{,sub/}*.md", {"rules/a.md", "rules/.dot.md", "rules/sub/b.md"}),
    ("rules/{a,b.md", set()),
    ("rules/a}.md", set()),
    ("rules/a}{.md,.txt}", set()),
    ("missing/*.md", set()),
    ("RULES/*.MD", {"rules/a.md", "rules/.dot.md"} if CASE_BLIND else set()),
    ("Top.md", {"top.md"} if CASE_BLIND else set()),
])
def test_instruction_patterns_match_the_files_kilo_matches(tmp_path, pattern, expected):
    assert _glob_project(tmp_path, pattern) == expected


@pytest.mark.parametrize("pattern", [
    "rules/!(a).md", "rules/@(a).md", "rules/+(a).md", "rules/*(a).md", "rules/?(a).md",
    "rules/{1..3}.md", "rules/{a}.md", "rules/[^a].md", "rules/[[:alpha:]].md", "rules/\\*.md", "!rules/a.md",
])
def test_glob_syntax_whitney_does_not_expand_as_kilo_does_is_refused_by_name(tmp_path, pattern):
    with pytest.raises(ValueError) as raised:
        _glob_project(tmp_path, pattern)
    assert str(raised.value) == (
        f"project instruction pattern {pattern!r} uses glob syntax Whitney doesn't expand as Kilo does; "
        "use *, ?, **, [...], [!...] and {a,b} only"
    )


@pytest.mark.parametrize("pattern", ["~/rules.md", "{rules,..}/a.md", "rules/{../x,a}.md"])
def test_a_pattern_that_reaches_outside_the_project_after_expansion_is_refused(tmp_path, pattern):
    with pytest.raises(ValueError, match=f"^project instruction path must stay inside the project: {re.escape(repr(pattern))}$"):
        _glob_project(tmp_path, pattern)


def test_double_star_does_not_follow_a_symlinked_folder_and_a_named_one_is_not_staged(tmp_path):
    _kilo_project(tmp_path, {"elsewhere/inner/t.md": "t\n", "rules/a.md": "a\n"})
    (tmp_path / "rules" / "linked").symlink_to(tmp_path / "elsewhere")
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["rules/**/*.md", "rules/linked/inner/t.md"]}))

    assert set(load_project_files(tmp_path)) == {"kilo.jsonc", "rules/a.md"}


def test_a_file_matched_by_two_patterns_is_staged_once_as_spelled_on_disk(tmp_path):
    _kilo_project(tmp_path, {"rules/a.md": "a\n"})
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["rules/*.md", "rules/a.md", "**/a.md"]}))

    assert set(load_project_files(tmp_path)) == {"kilo.jsonc", "rules/a.md"}


# Nothing under the evals path is staged, even when an instructions pattern matches it, so the
# agent never sees the scenarios, their expected results or their tokens (spec, "The two paths").


def test_nothing_under_the_evals_path_is_staged_even_when_an_instructions_pattern_matches_it(tmp_path):
    _kilo_project(tmp_path, {
        "AGENTS.md": "rule\n",
        "evals/agents-md.json": '{"secret": "CANARY-1"}',
        "evals/README.md": "how the evals work\n",
        "evals/fixtures/repo/project/AGENTS.md": "fixture rule\n",
        "evals/.kilo/skills/x/SKILL.md": "a skill under the evals path\n",
        "evals/bad.md": "",
    })
    (tmp_path / "evals/bad.md").write_bytes(b"\xff not UTF-8, and never read")
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md", "evals/*.json"]}))

    assert set(load_project_files(tmp_path, evals_path=tmp_path / "evals")) == {"kilo.jsonc", "AGENTS.md"}


def test_the_evals_path_is_compared_after_resolving_it(tmp_path, monkeypatch):
    _kilo_project(tmp_path, {"checks/README.md": "how the evals work\n", "rules/a.md": "a\n"})
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md"]}))
    monkeypatch.chdir(tmp_path / "rules")

    assert set(load_project_files(tmp_path, evals_path=Path("../checks"))) == {"kilo.jsonc", "rules/a.md"}


@pytest.mark.parametrize("load", [load_project_files, load_claude_code_project_files])
def test_a_project_inside_the_evals_path_is_refused(tmp_path, load):
    project = tmp_path / "evals" / "project"
    project.mkdir(parents=True)
    _kilo_project(project, {"AGENTS.md": "rule\n"})

    for evals in (tmp_path / "evals", project):
        with pytest.raises(ValueError) as raised:
            load(project, evals_path=evals)
        assert str(raised.value) == (
            f"project path {project.resolve()} is inside the evals path {evals.resolve()}, whose files are never staged"
        )


def test_a_claude_code_instruction_file_under_the_evals_path_is_refused_by_name(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("rule\n")

    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(tmp_path, evals_path=tmp_path / ".claude")

    assert str(raised.value) == (
        f"{tmp_path.resolve() / '.claude/CLAUDE.md'} is the instruction file to stage, but it is under the evals path "
        f"{(tmp_path / '.claude').resolve()}, whose files are never staged"
    )


def test_the_loader_for_a_target_passes_the_evals_path_on(tmp_path):
    _kilo_project(tmp_path, {"AGENTS.md": "rule\n", "evals/x.md": "x\n"})
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["evals/*.md"]}))

    assert "evals/x.md" in load_project_files_for("kilo", tmp_path)
    assert "evals/x.md" not in load_project_files_for("kilo", tmp_path, evals_path=tmp_path / "evals")
    with pytest.raises(ValueError, match="inside the evals path"):
        load_project_files_for("claude-code", tmp_path, evals_path=tmp_path)


def test_a_kilo_config_under_the_evals_path_is_neither_staged_nor_read(tmp_path):
    _kilo_project(tmp_path, {".kilo/kilo.jsonc": '{"instructions": ["rules/*.md"]}', "rules/a.md": "a\n"})

    assert set(load_project_files(tmp_path, evals_path=tmp_path / ".kilo")) == {"kilo.jsonc"}
    (tmp_path / "kilo.jsonc").unlink()
    with pytest.raises(ValueError, match="^project has no Kilo config"):
        load_project_files(tmp_path, evals_path=tmp_path / ".kilo")


# Why a file isn't staged, for validation rule 6: an eval file's `source` must be staged, and if the
# safety rules left it out, validation says which rule.


@pytest.fixture
def unsafe_project(tmp_path):
    (tmp_path / "project").mkdir()
    root = _kilo_project(tmp_path / "project", {
        "AGENTS.md": "rule\n", ".env": "SECRET=x\n", "app.py": "source\n", "node_modules/pkg/README.md": "dependency\n",
        "evals/README.md": "evals\n", "real/rules.md": "rule\n",
    })
    (tmp_path / "outside.md").write_text("outside\n")
    (root / "linked.md").symlink_to(tmp_path / "outside.md")
    (root / "linked").symlink_to(root / "real")
    (root / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md", "**/README.md"]}))
    return root


@pytest.mark.parametrize("source, reason", [
    ("../outside.md", "it isn't a path inside the project"),
    ("/etc/hosts", "it isn't a path inside the project"),
    ("~/notes.md", "it isn't a path inside the project"),
    ("missing.md", "the project has no such file"),
    ("evals/README.md", "it is under the evals path, whose files are never staged"),
    ("linked.md", "it is a symlink, or inside a symlinked folder, and nothing is staged through a link"),
    ("linked/rules.md", "it is a symlink, or inside a symlinked folder, and nothing is staged through a link"),
    ("real", "it is a folder, not a file"),
    ("node_modules/pkg/README.md", "it is inside node_modules/, which is never staged"),
    (".env", ".env files are never staged, except .env.example, .env.sample and .env.template"),
    ("app.py", (
        "the kilo loader stages only Kilo config, root AGENTS.md, CLAUDE.md and CONTEXT.md, the .kilo and "
        ".kilocode asset, rules and workflows folders, .claude/skills/, .agents/skills/, .kilocodemodes, the legacy "
        ".kilocoderules files, and files an instructions pattern matches"
    )),
])
def test_the_reason_a_file_is_not_staged_names_the_rule(unsafe_project, source, reason):
    staged = load_project_files(unsafe_project, evals_path=unsafe_project / "evals")
    assert source not in staged

    assert not_staged_reason("kilo", unsafe_project, source, staged, evals_path=unsafe_project / "evals") == reason


def test_a_staged_file_has_no_reason(unsafe_project):
    staged = load_project_files(unsafe_project)
    assert not_staged_reason("kilo", unsafe_project, "AGENTS.md", staged) is None
    assert not_staged_reason("kilo", unsafe_project, "evals/README.md", staged) is None  # no evals path given


@pytest.mark.parametrize("present, source, reason", [
    (["AGENTS.md", "CLAUDE.md"], "CLAUDE.md", "the claude-code loader stages one instruction file, and this one is beside AGENTS.md"),
    (["AGENTS.md", ".claude/settings.json"], ".claude/settings.json", "the claude-code loader stages one instruction file, and this one is rest of .claude/"),
    (["AGENTS.md", "notes.md"], "notes.md", "the claude-code loader stages only one of AGENTS.md, CLAUDE.md and .claude/CLAUDE.md, as AGENTS.md"),
])
def test_the_claude_code_reasons_are_the_reports(tmp_path, present, source, reason):
    for name in present:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x\n")
    staged = load_claude_code_project_files(tmp_path)

    assert not_staged_reason("claude-code", tmp_path, source, staged) == reason


def test_a_claude_code_file_staged_under_another_name_has_no_reason(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("rule\n")
    staged = load_claude_code_project_files(tmp_path)

    assert not_staged_reason("claude-code", tmp_path, "CLAUDE.md", staged) is None
    assert not_staged_reason("claude-code", tmp_path, "AGENTS.md", staged) is None


@pytest.mark.parametrize("case_blind, expected", [(True, {"rules/a.md", "rules/.dot.md"}), (False, set())])
def test_names_match_whatever_their_case_only_where_kilos_glob_does(tmp_path, monkeypatch, case_blind, expected):
    # Kilo's node-glob is case-blind on macOS and Windows and case-sensitive elsewhere, whatever the disk.
    monkeypatch.setattr(project_files, "GLOB_CASE_BLIND", case_blind)
    assert _glob_project(tmp_path, "RULES/*.MD") == expected
    assert _glob_project(tmp_path, "rules/*.md") == {"rules/a.md", "rules/.dot.md"}


def test_a_symlink_loop_does_not_stop_the_expansion(tmp_path):
    _kilo_project(tmp_path, {"rules/a.md": "a\n"})
    (tmp_path / "rules" / "loop").symlink_to(tmp_path)
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md", "rules/loop/rules/*.md"]}))

    assert set(load_project_files(tmp_path)) == {"kilo.jsonc", "rules/a.md"}


def test_nothing_in_a_staged_folder_under_the_evals_path_is_staged(tmp_path):
    _kilo_project(tmp_path, {".agents/skills/x/SKILL.md": "skill\n", ".claude/skills/y/SKILL.md": "skill\n"})
    (tmp_path / ".agents/skills/x/bad.md").write_bytes(b"\xff not UTF-8, and never read")

    assert set(load_project_files(tmp_path, evals_path=tmp_path / ".agents")) == {"kilo.jsonc", ".claude/skills/y/SKILL.md"}


# Review of Task 8 (2026-11-06): fixes and the decisions that came with it.


@pytest.mark.parametrize("linked", [".kilo", ".kilocode"])
def test_a_kilo_config_in_a_symlinked_folder_is_not_staged_even_into_the_evals_path(tmp_path, linked):
    _kilo_project(tmp_path, {"real/kilo.jsonc": '{"permission": {"bash": "deny"}}', "evals/kilo.jsonc": '{"secret": "CANARY-1"}'})
    (tmp_path / linked).symlink_to(tmp_path / "real")
    assert set(load_project_files(tmp_path)) == {"kilo.jsonc"}

    (tmp_path / linked).unlink()
    (tmp_path / linked).symlink_to(tmp_path / "evals")
    assert set(load_project_files(tmp_path, evals_path=tmp_path / "evals")) == {"kilo.jsonc"}


def test_a_root_instruction_file_in_a_symlinked_project_part_is_not_staged(tmp_path):
    # .kilocodemodes and the legacy rules files sit at the root, so only the file itself can be a link; a linked
    # .kilo/ is the folder case above. This pins that the root files keep the same rule as the folders.
    _kilo_project(tmp_path, {"real.md": "rule\n"})
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / "real.md")
    assert set(load_project_files(tmp_path)) == {"kilo.jsonc"}


def test_the_evals_path_is_matched_by_the_folder_it_names_not_by_its_spelling(tmp_path, case_insensitive):
    # On a case-insensitive disk, Evals/ is the evals/ folder; comparing spellings would stage it.
    _kilo_project(tmp_path, {"evals/notes.md": "evals\n", "rules/a.md": "a\n"})
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md"]}))

    staged = set(load_project_files(tmp_path, evals_path=tmp_path / "Evals"))

    assert staged == ({"kilo.jsonc", "rules/a.md"} if case_insensitive else {"kilo.jsonc", "rules/a.md", "evals/notes.md"})


def test_a_project_path_spelled_otherwise_is_still_matched_against_the_evals_path(tmp_path, case_insensitive):
    project = tmp_path / "project"
    project.mkdir()
    _kilo_project(project, {"evals/notes.md": "evals\n"})
    (project / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md"]}))
    spelled = tmp_path / "PROJECT" if case_insensitive else project

    staged = load_project_files(spelled, evals_path=project / "evals")

    assert set(staged) == {"kilo.jsonc"}
    assert not_staged_reason("kilo", spelled, "evals/notes.md", staged, evals_path=project / "evals") == (
        "it is under the evals path, whose files are never staged"
    )
    if case_insensitive:
        with pytest.raises(ValueError, match="inside the evals path"):
            load_project_files(project / "evals", evals_path=tmp_path / "PROJECT")


def test_a_file_in_a_skill_folder_that_is_not_utf8_is_left_out_and_named(tmp_path):
    # Skills can carry fonts and images; one such file mustn't stop every run of the project.
    _kilo_project(tmp_path, {
        ".claude/skills/x/SKILL.md": "skill x\n", ".agents/skills/y/SKILL.md": "skill y\n", ".kilo/skills/z/SKILL.md": "skill z\n",
    })
    for name in (".claude/skills/x/font.ttf", ".agents/skills/y/logo.png", ".kilo/skills/z/data.bin"):
        (tmp_path / name).write_bytes(b"\xff\xd8 binary")

    assert set(load_project_files(tmp_path)) == {
        "kilo.jsonc", ".claude/skills/x/SKILL.md", ".agents/skills/y/SKILL.md", ".kilo/skills/z/SKILL.md",
    }
    assert files_not_staged("kilo", tmp_path) == [".agents/skills/y/logo.png", ".claude/skills/x/font.ttf", ".kilo/skills/z/data.bin"]
    assert set(files_not_staged_reasons("kilo", tmp_path).values()) == {"not UTF-8 text"}


def test_an_instruction_file_that_is_not_utf8_still_stops_a_kilo_run(tmp_path):
    _kilo_project(tmp_path, {})
    (tmp_path / "AGENTS.md").write_bytes(b"\xff rules")
    with pytest.raises(ValueError, match="must be UTF-8 text"):
        load_project_files(tmp_path)


def test_workflows_and_kilocodemodes_kilo_reads_are_staged(tmp_path):
    # kilo debug config shows each workflow's .md as a command, and .kilocodemodes's modes as agents.
    _kilo_project(tmp_path, {
        ".kilo/workflows/release.md": "release\n",
        ".kilocode/workflows/review.md": "review\n",
        ".kilo/workflows/.hidden.md": "hidden\n",
        ".kilo/workflows/nested/deep.md": "not read\n",
        ".kilo/workflows/notes.txt": "not read\n",
        ".kilocode/workflows/UP.MD": "not read\n",
        ".kilocodemodes": "customModes:\n  - slug: reviewer\n    name: Reviewer\n    roleDefinition: You review.\n",
    })

    assert set(load_project_files(tmp_path)) == {
        "kilo.jsonc", ".kilo/workflows/release.md", ".kilocode/workflows/review.md", ".kilo/workflows/.hidden.md", ".kilocodemodes",
    }


def test_a_symlinked_kilocodemodes_is_not_staged(tmp_path):
    _kilo_project(tmp_path, {"real.yaml": "customModes: []\n"})
    (tmp_path / ".kilocodemodes").symlink_to(tmp_path / "real.yaml")
    assert set(load_project_files(tmp_path)) == {"kilo.jsonc"}


@pytest.mark.parametrize("config", ["kilo.jsonc", ".kilo/kilo.json"])
def test_skills_paths_is_stripped_like_the_connection_keys_and_named(tmp_path, config):
    _kilo_project(tmp_path, {})
    (tmp_path / "kilo.jsonc").unlink()
    (tmp_path / config).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / config).write_text(json.dumps({
        "skills": {"paths": ["../shared-skills"], "urls": ["https://example.invalid/skills/"]}, "permission": {"bash": "ask"},
    }))

    staged = parse_jsonc(load_project_files(tmp_path)[config])

    assert staged == {"skills": {"urls": ["https://example.invalid/skills/"]}, "permission": {"bash": "ask"}}
    assert files_not_staged("kilo", tmp_path) == [f"{config}: skills.paths"]
    assert files_not_staged_reasons("kilo", tmp_path) == {
        f"{config}: skills.paths": "left out of the staged config: its folders can be outside the project"
    }


def test_a_skills_block_holding_only_paths_is_left_out_whole(tmp_path):
    _kilo_project(tmp_path, {})
    (tmp_path / "kilo.jsonc").write_text('{"skills": {"paths": ["./my-skills"]}}')
    assert parse_jsonc(load_project_files(tmp_path)["kilo.jsonc"]) == {}


def test_a_kilo_project_with_nothing_left_out_names_nothing(tmp_path):
    _kilo_project(tmp_path, {"AGENTS.md": "rule\n", "app.py": "source\n"})
    assert files_not_staged("kilo", tmp_path) == []
    assert files_not_staged_reasons("kilo", tmp_path) == {}


@pytest.fixture
def unreadable(tmp_path):
    """Folders made unreadable by the test, given back their permissions afterwards."""
    folders = []

    def make(path):
        path.mkdir(parents=True, exist_ok=True)
        (path / "x.md").write_text("x\n")
        path.chmod(0)
        folders.append(path)

    yield make
    for path in folders:
        path.chmod(0o755)


@pytest.mark.parametrize("evals", ["evals", ".claude/skills/evals-inside"])
def test_the_evals_path_and_the_excluded_folders_are_never_looked_in(tmp_path, unreadable, evals):
    # An unreadable folder can't be listed, so the walk would fail if it looked in one.
    _kilo_project(tmp_path, {"rules/a.md": "a\n", ".claude/skills/x/SKILL.md": "x\n"})
    for folder in (evals, "node_modules/pkg", ".claude/skills/node_modules", ".git/objects"):
        unreadable(tmp_path / folder)
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md"]}))

    staged = set(load_project_files(tmp_path, evals_path=tmp_path / evals))

    assert staged == {"kilo.jsonc", "rules/a.md", ".claude/skills/x/SKILL.md"}


def test_a_pattern_that_expands_to_too_many_alternatives_is_refused_by_name(tmp_path):
    pattern = "{a,b}" * 11 + ".md"  # 2,048 alternatives
    with pytest.raises(ValueError) as raised:
        _glob_project(tmp_path, pattern)
    assert str(raised.value) == f"project instruction pattern {pattern!r} expands to more than 1024 patterns; split it into smaller ones"
    assert _glob_project(tmp_path, "{a,b}" * 10 + "x.md") == set()  # 1,024 is allowed


def test_repeated_double_stars_match_what_one_does(tmp_path):
    assert _glob_project(tmp_path, "rules/**/**/**/*.md") == _glob_project(tmp_path, "rules/**/*.md")


def test_a_path_outside_the_root_is_walked_up_to_the_top_and_found_unlinked(tmp_path):
    assert project_files._linked_below(tmp_path / "root", tmp_path / "elsewhere" / "file.md") is False


@pytest.mark.parametrize("source", [
    ".kilocode/rules/nested/deep.md", ".kilo/rules/notes.txt", ".kilo/workflows/nested/w.md", ".kilocode/rules-code/UP.MD",
])
def test_a_file_in_a_rules_or_workflows_folder_that_kilo_does_not_read_is_told_which_ones_it_does(tmp_path, source):
    _kilo_project(tmp_path, {source: "x\n"})
    staged = load_project_files(tmp_path)

    assert not_staged_reason("kilo", tmp_path, source, staged) == (
        "Kilo reads only the .md files directly in the rules and workflows folders"
    )


@pytest.mark.parametrize("source", ["./AGENTS.md", "AGENTS.md", ".//AGENTS.md"])
def test_a_source_spelled_with_dots_or_double_slashes_is_the_same_file(tmp_path, source):
    _kilo_project(tmp_path, {"AGENTS.md": "rule\n"})
    assert not_staged_reason("kilo", tmp_path, source, load_project_files(tmp_path)) is None


def test_a_skill_file_left_out_as_not_utf8_says_so(tmp_path):
    _kilo_project(tmp_path, {".agents/skills/y/SKILL.md": "skill\n"})
    (tmp_path / ".agents/skills/y/logo.png").write_bytes(b"\x89PNG\xff")

    assert not_staged_reason("kilo", tmp_path, ".agents/skills/y/logo.png", load_project_files(tmp_path)) == "it is not UTF-8 text"


def test_rules_and_workflows_under_the_evals_path_are_not_staged(tmp_path):
    _kilo_project(tmp_path, {".kilocode/rules/r.md": "rule\n", ".kilocode/workflows/w.md": "workflow\n", ".kilo/rules/k.md": "k\n"})
    assert set(load_project_files(tmp_path, evals_path=tmp_path / ".kilocode")) == {"kilo.jsonc", ".kilo/rules/k.md"}


def test_a_file_that_is_not_utf8_outside_the_skill_folders_still_stops_the_run(tmp_path):
    _kilo_project(tmp_path, {})
    (tmp_path / ".kilo/agents").mkdir(parents=True)
    (tmp_path / ".kilo/agents/helper.md").write_bytes(b"\xff agent")
    with pytest.raises(ValueError, match="must be UTF-8 text"):
        load_project_files(tmp_path)


def test_skill_files_are_read_as_utf8_whatever_the_locale(tmp_path, ascii_locale):
    _kilo_project(tmp_path, {})
    (tmp_path / ".agents/skills/x").mkdir(parents=True)
    (tmp_path / ".agents/skills/x/SKILL.md").write_text("- Règle\n", encoding="utf-8")
    assert load_project_files(tmp_path)[".agents/skills/x/SKILL.md"] == "- Règle\n"


def test_a_symlink_in_a_skill_folder_is_skipped_and_the_files_after_it_are_staged(tmp_path):
    _kilo_project(tmp_path, {"real.md": "x\n", ".claude/skills/x/SKILL.md": "skill\n", ".claude/skills/x/z.md": "z\n"})
    (tmp_path / ".claude/skills/x/A-link.md").symlink_to(tmp_path / "real.md")
    assert {name for name in load_project_files(tmp_path) if name.startswith(".claude/")} == {
        ".claude/skills/x/SKILL.md", ".claude/skills/x/z.md",
    }


@pytest.mark.parametrize("evals, pattern", [
    (".claude/skills/x/deep/evals", None),
    ("docs/a/evals", "**/*.md"),
    ("docs/evals", "docs/**/evals/*.md"),
    ("docs/a/evals", "docs/*/*/*.md"),
])
def test_an_evals_path_deep_in_a_walked_folder_is_never_looked_in(tmp_path, unreadable, evals, pattern):
    _kilo_project(tmp_path, {".claude/skills/x/SKILL.md": "x\n", "docs/a/d.md": "d\n"})
    unreadable(tmp_path / evals)
    if pattern:
        (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": [pattern]}))

    assert ".claude/skills/x/SKILL.md" in load_project_files(tmp_path, evals_path=tmp_path / evals)


def test_a_binary_skill_file_under_the_evals_path_is_not_named(tmp_path):
    _kilo_project(tmp_path, {".claude/skills/x/SKILL.md": "x\n"})
    (tmp_path / ".claude/skills/x/logo.png").write_bytes(b"\x89PNG\xff")
    evals = tmp_path / ".claude/skills/x"

    assert files_not_staged("kilo", tmp_path, evals_path=evals) == []
    assert files_not_staged_reasons("kilo", tmp_path, evals_path=evals) == {}
    assert not_staged_reason("kilo", tmp_path, ".claude/skills/x/logo.png", {}, evals_path=evals) == (
        "it is under the evals path, whose files are never staged"
    )
    assert files_not_staged("kilo", tmp_path) == [".claude/skills/x/logo.png"]


def test_an_evals_path_that_names_a_root_file_leaves_that_file_out(tmp_path):
    _kilo_project(tmp_path, {"AGENTS.md": "rules\n", "CONTEXT.md": "context\n", ".kilocoderules": "legacy\n"})

    assert set(load_project_files(tmp_path, evals_path=tmp_path / "CONTEXT.md")) == {"kilo.jsonc", "AGENTS.md", ".kilocoderules"}
    assert set(load_project_files(tmp_path, evals_path=tmp_path / ".kilocoderules")) == {"kilo.jsonc", "AGENTS.md", "CONTEXT.md"}


def test_the_reason_for_a_kilo_file_ignores_what_lies_under_the_evals_path(tmp_path):
    # The loader never reads the evals folder, so a file there that isn't UTF-8 can't stop the reason being given.
    _kilo_project(tmp_path, {"app.py": "source\n"})
    (tmp_path / "evals").mkdir()
    (tmp_path / "evals/bad.md").write_bytes(b"\xff not UTF-8")
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md"]}))
    evals = tmp_path / "evals"
    staged = load_project_files(tmp_path, evals_path=evals)

    assert not_staged_reason("kilo", tmp_path, "app.py", staged, evals_path=evals).startswith("the kilo loader stages only")


def test_a_broken_symlink_is_named_as_a_symlink_when_an_evals_path_is_given(tmp_path):
    _kilo_project(tmp_path, {"evals/x.md": "x\n"})
    (tmp_path / "broken.md").symlink_to(tmp_path / "missing.md")
    evals = tmp_path / "evals"
    staged = load_project_files(tmp_path, evals_path=evals)

    assert not_staged_reason("kilo", tmp_path, "broken.md", staged, evals_path=evals) == (
        "it is a symlink, or inside a symlinked folder, and nothing is staged through a link"
    )


def test_many_repeated_double_stars_are_expanded_as_one_without_walking_each(tmp_path):
    # Walked one by one, 40 "**" in a row try every way of sharing the 7 folders above x.md between
    # them (tens of millions of splits, which takes hours); folded into one, the walk is a single pass
    # (a few milliseconds). The load runs in a subprocess first, so an unfolded walk fails at 60 s
    # instead of hanging the run; counting directory listings instead would assert on a call count.
    # A first segment pair keeps the fold honest about where the run of "**" starts.
    _kilo_project(tmp_path, {"docs/rules/a/b/c/d/e/f/g/x.md": "x\n", "docs/rules/top.md": "top\n"})
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": ["docs/rules/" + "**/" * 40 + "*.md"]}))
    expected = ["docs/rules/a/b/c/d/e/f/g/x.md", "docs/rules/top.md", "kilo.jsonc"]
    code = "import sys; from whitney.project_files import load_project_files; print(sorted(load_project_files(sys.argv[1])))"

    run = _run_whitney(code, tmp_path, timeout=60)
    # Also called here, so mutation testing, which maps tests to the functions they call in-process, runs this test.
    staged = load_project_files(tmp_path)

    assert run.stdout.strip() == str(expected), run.stderr
    assert sorted(staged) == expected


def _run_whitney(code, *args, timeout, env=None):
    """Run `code` in a fresh Python that imports the whitney this test imported; past `timeout` seconds it is killed and the test fails."""
    import whitney

    env = {**os.environ, **(env or {}), "PYTHONPATH": str(Path(whitney.__file__).parent.parent)}
    return subprocess.run([sys.executable, "-c", code, *map(str, args)], capture_output=True, text=True, env=env, timeout=timeout, check=True)


def test_on_a_case_sensitive_disk_the_exact_spelling_is_named_over_another_case(tmp_path):
    # A case-sensitive folder can hold both CLAUDE.md and claude.md; the file found by its exact name is the one named.
    # Such a folder can't be made on a case-insensitive disk, so the listing is given directly.
    assert project_files._on_disk_name(["claude.md", "CLAUDE.md"], "CLAUDE.md") == "CLAUDE.md"
    assert project_files._on_disk_name(["claude.md"], "CLAUDE.md") == "claude.md"


def test_a_project_inside_a_folder_named_like_an_excluded_one_still_never_stages_env(tmp_path):
    project = tmp_path / "node_modules" / "proj"
    project.mkdir(parents=True)
    _kilo_project(project, {"AGENTS.md": "rules\n", ".env": "SECRET=1\n"})
    (project / "kilo.jsonc").write_text(json.dumps({"instructions": [".env"]}))

    assert ".env" not in load_project_files(project)


PROJECT_UNDER_TEST = {
    "AGENTS.md": "rules\n",
    ".kilo/rules/a.md": "rule a\n",
    ".kilo/skills/x/SKILL.md": "skill\n",
    "docs/notes.md": "notes\n",
    "node_modules/pkg/x.md": "a dependency's file\n",
    ".env": "SECRET=1\n",
    "app.py": "source\n",
}
STAGED_FROM_PROJECT_UNDER_TEST = {"kilo.jsonc", "AGENTS.md", ".kilo/rules/a.md", ".kilo/skills/x/SKILL.md", "docs/notes.md"}


@pytest.mark.parametrize("parent", ["plain", "node_modules", ".venv", ".git"])
def test_a_project_stages_the_same_files_whatever_the_folders_above_it_are_called(tmp_path, parent):
    # Only folders inside the project are excluded; the name of a folder above it says nothing about its files.
    project = tmp_path / parent / "proj"
    project.mkdir(parents=True)
    _kilo_project(project, PROJECT_UNDER_TEST)
    (project / "kilo.jsonc").write_text(json.dumps({"instructions": ["**/*.md", ".env"]}))

    staged = load_project_files(project)

    assert set(staged) == STAGED_FROM_PROJECT_UNDER_TEST
    assert set(load_template_files(project)) == STAGED_FROM_PROJECT_UNDER_TEST | {"app.py"}  # a template stages every text file
    assert not_staged_reason("kilo", project, "docs/notes.md", staged) is None
    assert not_staged_reason("kilo", project, "node_modules/pkg/x.md", staged) == (
        "it is inside node_modules/, which is never staged"
    )
    assert not_staged_reason("kilo", project, ".env", staged) == (
        ".env files are never staged, except .env.example, .env.sample and .env.template"
    )
    assert not_staged_reason("kilo", project, "app.py", staged).startswith("the kilo loader stages only")


def test_instructions_and_skill_folders_never_reach_into_an_excluded_folder(tmp_path):
    _kilo_project(tmp_path, {
        ".git/config": "[core]\n",
        "node_modules/x.md": "dependency\n",
        ".venv/a.md": "venv\n",
        "x/config": "plain\n",
        ".kilo/skills/s/SKILL.md": "skill\n",
        ".kilo/skills/s/node_modules/y.md": "dependency in a skill\n",
    })
    (tmp_path / "kilo.jsonc").write_text(json.dumps({"instructions": [".git/config", "node_modules/x.md", ".venv/a.md", "{.git,x}/config"]}))

    staged = load_project_files(tmp_path)

    assert set(staged) == {"kilo.jsonc", "x/config", ".kilo/skills/s/SKILL.md"}
    for relative, folder in [
        (".git/config", ".git"), ("node_modules/x.md", "node_modules"), (".venv/a.md", ".venv"),
        (".kilo/skills/s/node_modules/y.md", "node_modules"),
    ]:
        assert not_staged_reason("kilo", tmp_path, relative, staged) == f"it is inside {folder}/, which is never staged"
