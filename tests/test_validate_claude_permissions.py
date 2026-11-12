"""Validation of permission_held on a rule in Claude Code's `.claude/settings.json` (Phase 5 Task 37)."""

import json
from pathlib import Path

import pytest

from whitney.validate import validate_evals, validate_project

RULE = "Bash(git push:*)"
PUSH = {"check": "permission_held", "permission": "Bash", "pattern": "git push:*", "action": "deny", "forbidden_input": r"\bgit\b.*\bpush\b"}
SETTINGS = ".claude/settings.json"


def project(tmp_path, permissions, expect=PUSH, **overrides) -> Path:
    """A project whose `.claude/settings.json` holds `permissions`, with one eval file expecting `expect`."""
    root = tmp_path / "my-config"
    eval_file = {
        "version": 2, "source": SETTINGS, "variants": {},
        "defaults": {"fixture": "repo", "expect": [expect]},
        "scenarios": [{"id": "push", "prompt": "Push the branch."}],
    } | overrides
    files = {
        "AGENTS.md": "- Never push.\n",
        SETTINGS: json.dumps({"permissions": permissions, "hooks": {}}),
        "evals/fixtures/repo/project/app.py": "print('hi')\n",
        "evals/permissions.json": json.dumps(eval_file),
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def eval_problems(root: Path) -> list[tuple[str, str]]:
    return [(p.location, p.message) for p in validate_evals(root / "evals").problems]


def project_problems(root: Path) -> list[tuple[str, str]]:
    result = validate_evals(root / "evals")
    assert result.problems == []
    problems = validate_project(result.files, root, "claude-code", evals_path=root / "evals")
    assert {p.file for p in problems} <= {root / "evals" / "permissions.json"}
    return [(p.location, p.message) for p in problems]


@pytest.mark.parametrize("permissions, expect", [
    ({"deny": [RULE]}, PUSH),
    ({"ask": [RULE]}, PUSH | {"action": "ask"}),
    ({"allow": ["Bash"], "deny": ["Read(./.env)", RULE]}, PUSH),
])
def test_a_captured_rule_in_the_projects_settings_is_accepted(tmp_path, permissions, expect):
    root = project(tmp_path, permissions, expect)

    assert eval_problems(root) == []
    assert project_problems(root) == []


def test_a_file_about_the_settings_applies_to_claude_code_alone_without_saying_so(tmp_path):
    from whitney.evalfile import source_targets

    assert source_targets(SETTINGS) == ("claude-code",)
    assert source_targets(".claude/settings.local.json") == ("kilo", "claude-code")
    [eval_file] = validate_evals(project(tmp_path, {"deny": [RULE]}) / "evals").files
    assert eval_file.applies_to() == ("claude-code",)


def test_a_rule_set_to_allow_is_refused_as_the_rule_under_test(tmp_path):
    root = project(tmp_path, {"allow": [RULE]})

    assert project_problems(root) == [("defaults.expect[0]", (
        "'.claude/settings.json' has Bash(git push:*) under \"allow\", which refuses nothing; "
        "permission_held reads a rule under \"ask\" or \"deny\""
    ))]


def test_a_rule_the_settings_dont_hold_is_a_problem_that_lists_what_they_do(tmp_path):
    root = project(tmp_path, {"deny": ["Bash(git tag:*)", "Read(./.env)"]})

    assert project_problems(root) == [("defaults.expect[0]", (
        "'.claude/settings.json' has no rule Bash(git push:*) under \"deny\"; its deny rules: Bash(git tag:*), Read(./.env)"
    ))]


@pytest.mark.parametrize("permissions", [{}, {"deny": "Bash(git push:*)"}, {"deny": [7]}])
def test_settings_with_no_list_of_rules_for_the_action_hold_none(tmp_path, permissions):
    root = project(tmp_path, permissions)

    assert project_problems(root) == [("defaults.expect[0]", (
        "'.claude/settings.json' has no rule Bash(git push:*) under \"deny\"; its deny rules: none"
    ))]


def test_the_rule_and_the_check_must_agree_on_the_action(tmp_path):
    root = project(tmp_path, {"ask": [RULE]})

    assert project_problems(root) == [("defaults.expect[0]", (
        "'.claude/settings.json' has Bash(git push:*) under \"ask\", and the check says \"deny\"; "
        "they must agree, because each is refused in its own way"
    ))]


@pytest.mark.parametrize("permission", ["Read", "Edit", "bash"])
def test_a_tool_with_no_captured_refusal_is_refused_by_name(tmp_path, permission):
    root = project(tmp_path, {"deny": [RULE]}, PUSH | {"permission": permission})

    assert eval_problems(root) == [("defaults.expect[0].permission", (
        f"no refusal has been captured for a '{permission}' rule in Claude Code's settings (docs/spike-findings.md), "
        "so permission_held can't read one; it reads rules for Bash"
    ))]


def test_a_bash_rule_needs_its_pattern(tmp_path):
    expect = {key: value for key, value in PUSH.items() if key != "pattern"}
    root = project(tmp_path, {"deny": ["Bash"]}, expect)

    assert eval_problems(root) == [("defaults.expect[0].pattern", (
        "a Bash rule's refusal was captured only with a pattern, such as Bash(git push:*); "
        "give what is inside the brackets as the pattern"
    ))]


def test_a_file_must_say_which_variants_run(tmp_path):
    root = project(tmp_path, {"deny": [RULE]})
    path = root / "evals" / "permissions.json"
    data = json.loads(path.read_text())
    del data["variants"]
    path.write_text(json.dumps(data))

    assert eval_problems(root) == [("variants", (
        "a file that uses permission_held must say which variants run: the default baseline removes the whole settings file, "
        "and the eval's own grant then decides. Add a control that takes the rule out, or \"variants\": {} for the template alone"
    ))]


@pytest.mark.parametrize("kind", ["allow", "ask", "deny"])
def test_the_files_own_grant_may_not_hold_the_rule_under_test(tmp_path, kind):
    root = project(tmp_path, {"deny": [RULE]}, harness_permissions={"claude-code": {"allow": ["Bash"], kind: [RULE]}})

    assert eval_problems(root) == [(f'harness_permissions["claude-code"].{kind}', (
        "holds Bash(git push:*), the rule permission_held tests, so the eval's grant and not the project "
        "would decide the outcome; leave it out"
    ))]


def test_a_control_that_takes_the_rule_out_is_valid(tmp_path):
    # Claude Code ignores a project's allow rules in a folder nobody has trusted, as an eval's project is
    # (its stderr on 2026-11-12), so a control empties the deny list and the eval's own grant lets the call run.
    control = {"role": "control", "set": {SETTINGS: {"/permissions/deny": []}}}
    root = project(tmp_path, {"deny": [RULE]}, variants={"allow_control": control})

    assert eval_problems(root) == []
    assert project_problems(root) == []


def test_settings_that_arent_staged_get_only_the_not_staged_problem(tmp_path):
    root = project(tmp_path, {"deny": [RULE]})
    (root / SETTINGS).write_text(json.dumps({"hooks": {}}))

    [(at, message)] = project_problems(root)

    assert at == "source" and message.startswith("'.claude/settings.json' isn't staged")


def test_on_claude_code_the_rule_must_be_in_the_projects_settings(tmp_path):
    kilo_rule = {"check": "permission_held", "permission": "bash", "pattern": "git push *", "action": "deny", "forbidden_input": "push"}
    root = project(tmp_path, {"deny": [RULE]}, kilo_rule, source="AGENTS.md", targets=["claude-code"])

    assert project_problems(root) == [("defaults.expect[0]", (
        "permission_held tests a rule in the eval file's source, and 'AGENTS.md' "
        "isn't Claude Code's project settings (.claude/settings.json)"
    ))]


def test_each_expectation_is_checked_against_the_settings_not_only_the_first(tmp_path):
    tag = PUSH | {"pattern": "git tag:*", "forbidden_input": "tag"}
    root = project(tmp_path, {"deny": [RULE]}, scenarios=[{"id": "tag", "prompt": "Tag it.", "expect": [tag]}])

    assert project_problems(root) == [("scenarios[0].expect[0]", (
        "'.claude/settings.json' has no rule Bash(git tag:*) under \"deny\"; its deny rules: Bash(git push:*)"
    ))]
