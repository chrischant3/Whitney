import pytest

from whitney.project_files import load_project_files, load_template_files, set_external_directory
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
