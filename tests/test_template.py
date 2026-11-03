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



def test_each_problem_names_the_file_and_what_to_do(template):
    agents = template / "AGENTS.md"
    agents.write_text("- Use type hints.\n")
    assert check_template(template) == [f"{agents}: core rule '- {CORE_RULE}' has been removed"]
    agents.unlink()
    assert check_template(template) == [f"{agents}: missing; the harness loads rules from AGENTS.md at the project root"]


def test_a_stray_dot_kilo_agents_md_is_named_by_its_path(template):
    stray = template / ".kilo" / "AGENTS.md"
    stray.parent.mkdir()
    stray.write_text(f"- {CORE_RULE}\n")
    assert check_template(template) == [
        f"{stray}: Kilo doesn't load .kilo/AGENTS.md on its own; move its rules to the root AGENTS.md"
    ]


def test_an_unsafe_external_directory_quotes_what_was_found(template):
    config = template / "kilo.jsonc"
    config.write_text('{"permission": {"external_directory": {"*": "allow"}}}')
    assert check_template(template) == [
        f'{config}: permission.external_directory must be "ask" or "deny" (found {{"*": "allow"}})'
    ]


@pytest.mark.parametrize(
    "text, parsed",
    [
        ('{"a": "x\\"y"}', {"a": 'x"y'}),  # an escaped quote doesn't end the string
        ('{"a": "\\"// not a comment"}', {"a": '"// not a comment'}),  # nor when it comes first
        ('{"a": "*/", /* c */ "b": 1}', {"a": "*/", "b": 1}),  # a close marker inside a string isn't one
        ('{/* a */ "x": 1, /* b */ "y": 2}', {"x": 1, "y": 2}),  # each block comment ends at its own close
        ('{"a": 1 /**/, "b": 2}', {"a": 1, "b": 2}),  # an empty block comment
        ('{/*/ still a comment */ "a": 1}', {"a": 1}),  # "/*/" doesn't close itself
        ('{"a": 1 /* c */}', {"a": 1}),  # the text right after a comment is kept
        ('{"a": 1} /* never closed', {"a": 1}),
        ('{"a": ",}"}', {"a": ",}"}),  # a comma inside a string isn't trailing
        ('[{"a": 1,}, 2]', [{"a": 1}, 2]),  # a trailing comma before "}" that a comma follows
        ("[1, 2,]", [1, 2]),
        ("[1,\n  ]", [1]),
    ],
)
def test_parse_jsonc_edge_cases(text, parsed):
    assert parse_jsonc(text) == parsed


def test_an_escaped_backslash_can_end_a_string():
    assert parse_jsonc('{"a": "x\\\\"} // comment') == {"a": "x\\"}


def test_main_fails_if_any_folder_has_a_problem_not_just_the_last(template, tmp_path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "AGENTS.md").write_text(f"- {CORE_RULE}\n")
    (template / "AGENTS.md").unlink()
    assert main(["--target", "claude-code", str(template), str(good)]) == 1


def test_an_unterminated_string_is_a_parse_error_not_a_crash():
    import json

    with pytest.raises(json.JSONDecodeError):
        parse_jsonc('{"a": "never closed')


def test_main_says_ok_or_counts_the_problems_per_folder(template, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path.parent)  # the folder is shown relative to here, or as given when outside
    assert main([str(template)]) == 0
    (template / "AGENTS.md").unlink()
    (template / "kilo.jsonc").unlink()
    assert main([str(template)]) == 1
    lines = capsys.readouterr().out.splitlines()
    shown = str(template.resolve().relative_to(tmp_path.parent.resolve()))
    assert lines[0] == f"{shown} (kilo): OK"
    assert lines[-1] == f"{shown} (kilo): 2 problem(s)"

    monkeypatch.chdir(template)
    elsewhere = tmp_path.parent / "not-under-cwd"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / "AGENTS.md").write_text(f"- {CORE_RULE}\n")
    main(["--target", "claude-code", str(elsewhere)])
    assert capsys.readouterr().out.splitlines() == [f"{elsewhere} (claude-code): OK"]


def test_help_and_target_choices(capsys, monkeypatch):
    import re

    monkeypatch.setenv("COLUMNS", "200")  # one line per option
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert "\nModel-free checks that a template is shaped the way the evals assume.\n" in out
    assert "Run against the shipped templates" not in out  # only the docstring's first line
    assert re.search(r"\n +folders +template folders to check \(default: every shipped template\)\n", out)
    assert re.search(r"--target \{kilo,claude-code\}\s+which harness the folders are for\n", out)
    with pytest.raises(SystemExit) as raised:
        main(["--target", "vim", "folder"])
    assert raised.value.code == 2
