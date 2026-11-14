"""The consumer example in examples/consumer: a project with its own rules and evals, as a fork would write them."""

import tomllib
from pathlib import Path

import pytest

from whitney import cli, tasks
from whitney.evalfile import TRIGGER_CONTROL, discover

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = ROOT / "examples" / "consumer"


def test_the_consumer_example_validates_for_kilo_with_no_problems_or_warnings(capsys):
    code = cli.main(["validate", "--target", "kilo", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 9 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_consumer_example_tests_each_of_its_rule_files_with_both_new_checks():
    files = {f.source: f for f in discover(CONSUMER / "evals") if f.source.endswith((".kilo/rules/data-handling.md", "AGENTS.md"))}

    assert set(files) == {".kilo/rules/data-handling.md", "AGENTS.md"}
    checks = {
        source: {e.check for scenario in f.resolved() for e in scenario.expect} for source, f in files.items()
    }
    assert checks == {".kilo/rules/data-handling.md": {"no_string_added", "no_secret_committed"}, "AGENTS.md": {"no_file_edits"}}


@pytest.mark.parametrize("folder", ["examples/", ".github/risk/"])
def test_mutmut_copies_the_folders_the_tests_read(folder):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]

    assert folder in config["also_copy"]


def test_the_example_skill_and_agent_each_have_trigger_tests_and_scenarios():
    files = {f.source: f for f in discover(CONSUMER / "evals")}
    skill, agent = files[".kilo/skills/release-notes/SKILL.md"], files[".kilo/agents/reviewer.md"]

    for eval_file in (skill, agent):
        kinds = [scenario.trigger for scenario in eval_file.resolved()]
        assert {None, "positive", "negative"} <= set(kinds)
    # One of the skill's negative prompts belongs to another skill, which must load instead.
    assert [prompt.owner for prompt in skill.trigger.negative if prompt.owner] == ["debugging"]
    assert (CONSUMER / ".kilo" / "skills" / "debugging" / "SKILL.md").is_file()
    # The agent's scenarios run it as the primary agent; its trigger tests don't.
    assert {scenario.agent for scenario in agent.resolved() if scenario.trigger is None} == {"reviewer"}
    assert {scenario.agent for scenario in agent.resolved() if scenario.trigger} == {None}


def test_the_example_skill_and_agent_apply_to_kilo_only():
    from whitney.checks import registry

    for name in ("skills/release-notes.json", "agents/reviewer.json"):
        [eval_file] = [f for f in discover(CONSUMER / "evals") if f.path.relative_to(CONSUMER / "evals").as_posix() == name]
        assert cli.why_not_run(eval_file, "kilo", registry()) is None
        assert cli.why_not_run(eval_file, "claude-code", registry()) is not None


def test_the_consumer_example_validates_for_claude_code_with_no_problems_or_warnings(capsys):
    code = cli.main(["validate", "--target", "claude-code", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 9 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_consumer_examples_push_rule_is_tested_on_each_harness_from_its_own_settings():
    files = {f.source: f for f in discover(CONSUMER / "evals") if f.source in ("kilo.jsonc", ".claude/settings.json")}

    assert {source: f.applies_to() for source, f in files.items()} == {"kilo.jsonc": ("kilo",), ".claude/settings.json": ("claude-code",)}
    for f in files.values():
        assert {e.check for scenario in f.resolved() for e in scenario.expect} == {"permission_held"}
        assert [variant.role for variant in (f.variants or {}).values()] == ["control"]


def test_the_consumer_example_validates_for_every_target_when_none_is_named(capsys):
    code = cli.main(["validate", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 9 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def _rules(relative: str) -> list[str]:
    """The bullet lines of one of the example's instruction files."""
    return [line for line in (CONSUMER / relative).read_text(encoding="utf-8").splitlines() if line.startswith("- ")]


def test_the_examples_claude_md_holds_every_rule_kilo_reads_with_no_imports():
    # Claude Code reads CLAUDE.md in place of AGENTS.md, so the example repeats the rules in it word for word.
    # It was written before the claude-code loader copied @path imports (2026-11-12); repeating still keeps
    # each rule in one file both harnesses read as text.
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


def test_each_harness_reads_its_own_folder_and_the_two_copies_of_each_skill_match():
    from whitney.project_files import load_project_files_for

    kilo = load_project_files_for("kilo", CONSUMER, evals_path=CONSUMER / "evals")
    claude = load_project_files_for("claude-code", CONSUMER, evals_path=CONSUMER / "evals")

    for skill in ("release-notes", "debugging"):
        kilo_copy, claude_copy = f".kilo/skills/{skill}/SKILL.md", f".claude/skills/{skill}/SKILL.md"
        assert kilo[kilo_copy] == claude[claude_copy]
        assert kilo_copy not in claude
        # Kilo reads .claude/skills/ too, so a Kilo run holds both copies.
        assert claude_copy in kilo


def test_the_kilo_skill_evals_baseline_removes_every_copy_kilo_stages_and_the_claude_one_runs_on_claude_code():
    from whitney.checks import registry
    from whitney.project_files import load_project_files_for
    from whitney.variants import apply_variant, declared_variants

    files = {f.source: f for f in discover(CONSUMER / "evals")}
    kilo_skill, claude_skill = files[".kilo/skills/release-notes/SKILL.md"], files[".claude/skills/release-notes/SKILL.md"]
    staged = load_project_files_for("kilo", CONSUMER, evals_path=CONSUMER / "evals")
    without = apply_variant(staged, declared_variants(kilo_skill)["baseline"])

    assert sorted(path for path in staged if "/release-notes/" in path) == [
        ".claude/skills/release-notes/SKILL.md",
        ".kilo/skills/release-notes/SKILL.md",
    ]
    assert not [path for path in without if "/release-notes/" in path]
    assert {t for t in ("kilo", "claude-code") if cli.why_not_run(claude_skill, t, registry()) is None} == {"claude-code"}
    assert {None, "positive", "negative"} <= {scenario.trigger for scenario in claude_skill.resolved()}
    assert [prompt.owner for prompt in claude_skill.trigger.negative if prompt.owner] == ["debugging"]


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


def test_the_claude_reviewer_file_is_claude_codes_alone_and_keeps_the_built_in_review_skill_out():
    # In the recorded run Haiku handed both scenarios to Claude Code's built-in code-review skill,
    # so the project's reviewer was never the one tested (docs/results/2026-11-12-claude-code-example.md).
    import json

    eval_file = json.loads((CONSUMER / "evals/agents/claude-reviewer.json").read_text())

    assert eval_file["targets"] == ["claude-code"]
    assert "Skill(code-review)" in eval_file["harness_permissions"]["claude-code"]["deny"]


def test_on_kilo_the_consumer_examples_trigger_control_stages_no_release_notes_skill():
    """The example keeps the skill in .kilo/skills/ and .claude/skills/; Kilo reads both, so the control removes both."""
    task = tasks.scenario_eval(
        "skills/release-notes.json", TRIGGER_CONTROL, target="kilo", model="test-provider/test-model",
        evals_path=str(CONSUMER / "evals"), project_path=str(CONSUMER),
    )

    staged = task.metadata["evaluation_assets"]
    assert [path for path in staged if "/release-notes/" in path] == []
    assert {".kilo/skills/debugging/SKILL.md", ".claude/skills/debugging/SKILL.md"} <= set(staged)
