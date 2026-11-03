from pathlib import Path

import pytest

from whitney.static_checks import CORE_RULE, check_template, main, parse_jsonc

SHIPPED_TEMPLATES = Path(__file__).parent.parent / "template"
SHIPPED_TEMPLATE = SHIPPED_TEMPLATES / "kilo"

GOOD_CONFIG = """{
  // comments are allowed in kilo.jsonc
  "$schema": "https://kilo.ai/config.json",
  "permission": {
    "external_directory": "ask", /* block comment */
  },
}
"""


@pytest.fixture
def template(tmp_path):
    (tmp_path / "AGENTS.md").write_text(f"# Rules\n\n- {CORE_RULE}\n")
    (tmp_path / "kilo.jsonc").write_text(GOOD_CONFIG)
    return tmp_path


def test_shipped_template_passes():
    assert check_template(SHIPPED_TEMPLATE) == []


def test_minimal_good_template_passes(template):
    assert check_template(template) == []


def test_team_additions_are_fine_while_core_rule_remains(template):
    (template / "AGENTS.md").write_text(
        f"# Team rules\n\n- Use type hints.\n* {CORE_RULE.lower()}\n- Write tests first.\n"
    )
    assert check_template(template) == []


def test_missing_agents_md(template):
    (template / "AGENTS.md").unlink()
    assert any("AGENTS.md" in p and "missing" in p for p in check_template(template))


def test_team_edit_that_drops_core_rule(template):
    (template / "AGENTS.md").write_text("# Team rules\n\n- Use type hints.\n")
    assert any(CORE_RULE in p for p in check_template(template))


def test_rule_mentioned_only_inside_a_sentence_does_not_count(template):
    (template / "AGENTS.md").write_text(f"We used to say '{CORE_RULE}' but dropped it.\n")
    assert any(CORE_RULE in p for p in check_template(template))


def test_missing_config(template):
    (template / "kilo.jsonc").unlink()
    assert any("kilo.jsonc" in p and "missing" in p for p in check_template(template))


def test_invalid_config(template):
    (template / "kilo.jsonc").write_text('{ "permission": ')
    assert any("kilo.jsonc" in p and "parse" in p for p in check_template(template))


@pytest.mark.parametrize("value", ['"allow"', "{}", '{"*": "allow"}'])
def test_external_directory_must_be_ask_or_deny(template, value):
    (template / "kilo.jsonc").write_text(f'{{"permission": {{"external_directory": {value}}}}}')
    assert any("external_directory" in p for p in check_template(template))


def test_external_directory_not_set(template):
    (template / "kilo.jsonc").write_text('{"permission": {"bash": "ask"}}')
    assert any("external_directory" in p for p in check_template(template))


@pytest.mark.parametrize("value", ['"ask"', '"deny"', '{"*": "ask", "/tmp/*": "allow"}'])
def test_external_directory_ask_deny_and_pattern_forms_pass(template, value):
    (template / "kilo.jsonc").write_text(f'{{"permission": {{"external_directory": {value}}}}}')
    assert check_template(template) == []


def test_dot_kilo_agents_md_is_flagged_because_kilo_ignores_it(template):
    (template / ".kilo").mkdir()
    (template / ".kilo" / "AGENTS.md").write_text(f"- {CORE_RULE}\n")
    assert any(".kilo/AGENTS.md" in p for p in check_template(template))


def test_parse_jsonc_keeps_double_slashes_inside_strings():
    assert parse_jsonc('{"url": "https://x.test/a//b"} // trailing') == {"url": "https://x.test/a//b"}


def test_main_exit_codes(template, capsys):
    assert main([str(template)]) == 0
    (template / "AGENTS.md").unlink()
    assert main([str(template)]) == 1
    assert "AGENTS.md" in capsys.readouterr().out


def test_shipped_claude_code_template_passes():
    assert check_template(SHIPPED_TEMPLATES / "claude-code", target="claude-code") == []


def test_claude_code_template_needs_only_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text(f"- {CORE_RULE}\n")
    assert check_template(tmp_path, target="claude-code") == []


def test_claude_code_template_still_needs_the_core_rule(tmp_path):
    (tmp_path / "AGENTS.md").write_text("- Use type hints.\n")
    assert any(CORE_RULE in p for p in check_template(tmp_path, target="claude-code"))


def test_unknown_target_is_rejected(template):
    with pytest.raises(ValueError, match="target must be one of kilo, claude-code; got .vim."):
        check_template(template, target="vim")


def test_main_without_folders_checks_every_shipped_template(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "template/kilo" in out and "template/claude-code" in out


def test_main_checks_a_folder_as_the_target_given(tmp_path):
    (tmp_path / "AGENTS.md").write_text(f"- {CORE_RULE}\n")
    assert main(["--target", "claude-code", str(tmp_path)]) == 0
    assert main([str(tmp_path)]) == 1  # as a kilo template it lacks kilo.jsonc
