import os
from pathlib import Path

import pytest

from whitney.project_files import (
    files_not_staged,
    files_not_staged_reasons,
    files_renamed,
    load_claude_code_project_files,
    load_project_files,
    load_project_files_for,
    load_template_files,
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
