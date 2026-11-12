"""The consumer example in examples/consumer: a project with its own rules and evals, as a fork would write them."""

import tomllib
from pathlib import Path

import pytest

from whitney import cli
from whitney.evalfile import discover

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = ROOT / "examples" / "consumer"


def test_the_consumer_example_validates_for_kilo_with_no_problems_or_warnings(capsys):
    code = cli.main(["validate", "--target", "kilo", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 8 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_consumer_example_tests_each_of_its_rule_files_with_both_new_checks():
    files = {f.source: f for f in discover(CONSUMER / "evals") if f.source.endswith((".kilo/rules/data-handling.md", "AGENTS.md"))}

    assert set(files) == {".kilo/rules/data-handling.md", "AGENTS.md"}
    checks = {
        source: {e.check for scenario in f.resolved() for e in scenario.expect} for source, f in files.items()
    }
    assert checks == {".kilo/rules/data-handling.md": {"no_string_added", "no_secret_committed"}, "AGENTS.md": {"no_file_edits"}}


@pytest.mark.parametrize("folder", ["examples/"])
def test_mutmut_copies_the_folders_the_tests_read(folder):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]

    assert folder in config["also_copy"]


def test_the_example_skill_and_agent_each_have_trigger_tests_and_scenarios():
    files = {f.source: f for f in discover(CONSUMER / "evals")}
    skill, agent = files[".claude/skills/release-notes/SKILL.md"], files[".kilo/agents/reviewer.md"]

    for eval_file in (skill, agent):
        kinds = [scenario.trigger for scenario in eval_file.resolved()]
        assert {None, "positive", "negative"} <= set(kinds)
    # One of the skill's negative prompts belongs to another skill, which must load instead.
    assert [prompt.owner for prompt in skill.trigger.negative if prompt.owner] == ["debugging"]
    assert (CONSUMER / ".claude" / "skills" / "debugging" / "SKILL.md").is_file()
    # The agent's scenarios run it as the primary agent; its trigger tests don't.
    assert {scenario.agent for scenario in agent.resolved() if scenario.trigger is None} == {"reviewer"}
    assert {scenario.agent for scenario in agent.resolved() if scenario.trigger} == {None}


def test_the_example_skill_applies_to_both_targets_and_the_kilo_agent_to_kilo_only():
    from whitney.checks import registry

    for name, on_claude_code in (("skills/release-notes.json", True), ("agents/reviewer.json", False)):
        [eval_file] = [f for f in discover(CONSUMER / "evals") if f.path.relative_to(CONSUMER / "evals").as_posix() == name]
        assert cli.why_not_run(eval_file, "kilo", registry()) is None
        assert (cli.why_not_run(eval_file, "claude-code", registry()) is None) == on_claude_code


def test_the_consumer_example_validates_for_claude_code_with_no_problems_or_warnings(capsys):
    code = cli.main(["validate", "--target", "claude-code", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 8 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_consumer_examples_push_rule_is_tested_on_each_harness_from_its_own_settings():
    files = {f.source: f for f in discover(CONSUMER / "evals") if f.source in ("kilo.jsonc", ".claude/settings.json")}

    assert {source: f.applies_to() for source, f in files.items()} == {"kilo.jsonc": ("kilo",), ".claude/settings.json": ("claude-code",)}
    for f in files.values():
        assert {e.check for scenario in f.resolved() for e in scenario.expect} == {"permission_held"}
        assert [variant.role for variant in (f.variants or {}).values()] == ["control"]


def test_the_consumer_example_validates_for_every_target_when_none_is_named(capsys):
    code = cli.main(["validate", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 8 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def _rules(relative: str) -> list[str]:
    """The bullet lines of one of the example's instruction files."""
    return [line for line in (CONSUMER / relative).read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def test_the_examples_claude_md_holds_every_rule_kilo_reads_with_no_imports():
    # Claude Code reads CLAUDE.md in place of AGENTS.md, and Whitney doesn't copy a file a CLAUDE.md
    # imports with @path, so the rules are repeated in it word for word.
    claude_md = _rules("CLAUDE.md")

    for relative in ("AGENTS.md", ".kilo/rules/data-handling.md"):
        assert _rules(relative)
        assert set(_rules(relative)) <= set(claude_md)
    assert "@" not in (CONSUMER / "CLAUDE.md").read_text(encoding="utf-8")


def test_claude_code_stages_the_examples_claude_files_and_not_its_agents_md():
    from whitney.project_files import load_project_files_for

    staged = load_project_files_for("claude-code", CONSUMER, evals_path=CONSUMER / "evals")

    assert set(staged) == {
        "CLAUDE.md",
        ".claude/settings.json",
        ".claude/agents/reviewer.md",
        ".claude/skills/release-notes/SKILL.md",
        ".claude/skills/debugging/SKILL.md",
    }


def test_each_harness_is_given_one_release_notes_skill():
    from whitney.project_files import load_project_files_for

    for target in ("kilo", "claude-code"):
        staged = load_project_files_for(target, CONSUMER, evals_path=CONSUMER / "evals")
        assert [path for path in staged if path.endswith("/release-notes/SKILL.md")] == [".claude/skills/release-notes/SKILL.md"]


@pytest.mark.parametrize(
    ("eval_name", "target", "rules_file"),
    [
        ("agents-md.json", "kilo", "AGENTS.md"),
        ("agents-md.json", "claude-code", "AGENTS.md"),
        ("rules/data-handling-md.json", "kilo", ".kilo/rules/data-handling.md"),
        ("claude-md.json", "claude-code", ".kilo/rules/data-handling.md"),
    ],
)
def test_each_rule_evals_template_holds_its_rules_and_its_baseline_none_of_them(eval_name, target, rules_file):
    from whitney.checks import registry
    from whitney.project_files import load_project_files_for
    from whitney.variants import apply_variant, declared_variants, for_claude_code_instructions

    [eval_file] = [f for f in discover(CONSUMER / "evals") if f.path.relative_to(CONSUMER / "evals").as_posix() == eval_name]
    staged = load_project_files_for(target, CONSUMER, evals_path=CONSUMER / "evals")
    baseline = declared_variants(eval_file)["baseline"]
    if target == "claude-code":
        baseline = for_claude_code_instructions(baseline, staged)
    without = apply_variant(staged, baseline)

    assert cli.why_not_run(eval_file, target, registry()) is None
    for rule in _rules(rules_file):
        assert any(rule in text for text in staged.values())
        assert not any(rule in text for text in without.values())


def test_the_rule_evals_apply_to_the_targets_whose_files_hold_the_rules():
    from whitney.checks import registry

    applies = {
        f.path.relative_to(CONSUMER / "evals").as_posix(): {t for t in ("kilo", "claude-code") if cli.why_not_run(f, t, registry()) is None}
        for f in discover(CONSUMER / "evals")
    }

    assert applies["claude-md.json"] == {"claude-code"}
    assert applies["rules/data-handling-md.json"] == {"kilo"}
    assert applies["agents-md.json"] == {"kilo", "claude-code"}


def test_the_claude_code_reviewer_is_tested_on_claude_code_with_trigger_tests_and_scenarios():
    from whitney.checks import registry

    [agent] = [f for f in discover(CONSUMER / "evals") if f.source == ".claude/agents/reviewer.md"]
    text = (CONSUMER / ".claude" / "agents" / "reviewer.md").read_text(encoding="utf-8")

    assert {t for t in ("kilo", "claude-code") if cli.why_not_run(agent, t, registry()) is None} == {"claude-code"}
    assert {None, "positive", "negative"} <= {scenario.trigger for scenario in agent.resolved()}
    # Claude Code offers an agent under its frontmatter name, and has no `kilo run --agent`.
    assert "\nname: reviewer\n" in text
    assert {scenario.agent for scenario in agent.resolved()} == {None}
    assert {e.check for scenario in agent.resolved() if scenario.trigger is None for e in scenario.expect} == {"no_file_edits"}
