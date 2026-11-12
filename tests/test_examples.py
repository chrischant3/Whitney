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

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 6 eval file(s), 0 problem(s), 0 warning(s)"
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

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 6 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_consumer_examples_push_rule_is_tested_on_each_harness_from_its_own_settings():
    files = {f.source: f for f in discover(CONSUMER / "evals") if f.source in ("kilo.jsonc", ".claude/settings.json")}

    assert {source: f.applies_to() for source, f in files.items()} == {"kilo.jsonc": ("kilo",), ".claude/settings.json": ("claude-code",)}
    for f in files.values():
        assert {e.check for scenario in f.resolved() for e in scenario.expect} == {"permission_held"}
        assert [variant.role for variant in (f.variants or {}).values()] == ["control"]
