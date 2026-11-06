import pytest

from whitney.project_files import (
    files_not_staged,
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


# The claude-code target's loader. Claude Code reads root AGENTS.md and CLAUDE.md,
# and .claude/CLAUDE.md (tasks/plan-phase5.md, decision 5); nothing else is staged.


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md"])
def test_a_claude_code_project_with_one_instruction_file_stages_it_without_kilo_config(tmp_path, name):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("- Team rule\n")
    assert load_claude_code_project_files(tmp_path) == {name: "- Team rule\n"}


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
        "CLAUDE.md": "claude rule\n",
        ".claude/CLAUDE.md": "folder rule\n",
    }


def test_a_claude_code_project_without_instructions_is_told_which_files_were_looked_for(tmp_path):
    (tmp_path / "kilo.jsonc").write_text("{}")
    with pytest.raises(ValueError) as raised:
        load_claude_code_project_files(tmp_path)
    assert str(raised.value) == (
        f"project has no Claude Code instructions (AGENTS.md, CLAUDE.md, .claude/CLAUDE.md): {tmp_path.resolve()}"
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

    assert load_claude_code_project_files(project) == {".claude/CLAUDE.md": "folder rule\n"}


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

    assert files_not_staged("claude-code", tmp_path) == [".claude/settings.json", ".claude/skills/"]
    assert files_not_staged("kilo", tmp_path) == []


def test_nothing_is_named_when_there_is_no_dot_claude_folder_or_it_holds_only_claude_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("rule\n")
    assert files_not_staged("claude-code", tmp_path) == []
    (tmp_path / ".claude").write_text("a file, not a folder")
    assert files_not_staged("claude-code", tmp_path) == []
    (tmp_path / ".claude").unlink()
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/CLAUDE.md").write_text("folder rule\n")
    assert files_not_staged("claude-code", tmp_path) == []


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
