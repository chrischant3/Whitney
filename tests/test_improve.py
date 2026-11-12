"""The improvement report (whitney.improve): what the runs and the text of each file under test say to change."""

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from whitney.improve import improvement_report, text_findings

LABELS = ["followed", "violated", "inconclusive"]
SKILL = ".claude/skills/release-notes/SKILL.md"
SOUND_SKILL = (
    "---\nname: release-notes\n"
    "description: Write release notes. Use when the user asks for release notes or a changelog entry.\n"
    "---\n\n# Release notes\n\n1. Read the merged changes.\n"
)


def run(scenario, answer="followed", *, used=None, pressure="none", trigger=None, said=None):
    """One sample: a scenario's run, scored `answer`; `used` says whether the skill under test loaded, `said` explains."""
    metadata = {"pressure": pressure, **({"whitney_trigger": trigger} if trigger else {})}
    score = NS(answer=answer, metadata={} if used is None else {"used": used}, explanation=said)
    return NS(id=scenario, error=None, scores={"s": score}, metadata=metadata)


def log(
    samples, *, variant="template", role="template", source="AGENTS.md", files=None, target="claude-code",
    eval_file="team.json", title="Team rule",
):
    """A log as scenario_eval writes it, for one variant of an eval file testing `source`."""
    under_test = None
    if source.endswith("SKILL.md"):
        under_test = {"kind": "skill", "name": source.split("/")[-2]}
    metadata = {
        "variant": variant, "variant_role": role, "eval_name": "team_rules", "eval_file": eval_file,
        "title": title, "source": source, "under_test": under_test, "labels": LABELS, "target": target,
        "checks": {"no_secret_committed": {"labels": LABELS, "judges": "agent", "min_pass_rate": 0.8}},
        "min_pass_rate": None, "evaluation_assets": dict(files or {}),
    }
    return NS(eval=NS(task=f"team_rules_{variant}", metadata=metadata), samples=list(samples), status="success")


def held_twice(*scenarios, **options):
    """Two epochs of each scenario, every run passing."""
    return [run(s, **options) for s in scenarios for _ in range(2)]


def text_rules(path, text, **more):
    """The ids of the text rules that fire on `path`, holding `text`, beside the files in `more`."""
    return [f.rule for f in text_findings(path, {path: text, **more})]


# --- rule 1: prompt engineering practice -------------------------------------------------------


def test_a_rule_that_only_says_what_not_to_do_is_quoted_with_its_line():
    files = {"AGENTS.md": "# Rules\n\n- Never commit .env files or secrets\n"}

    report = improvement_report([log(held_twice("a"), files=files)])

    assert "`say-what-to-do`" in report
    assert "line 3: `Never commit .env files or secrets`" in report


@pytest.mark.parametrize("line", [
    "- Never commit .env files. Put placeholders in .env.example instead.",
    "- Never commit .env files unless they are templates.",
    "- Commit .env.example, never .env.",
])
def test_a_rule_that_says_what_to_do_is_left_alone(line):
    assert "say-what-to-do" not in text_rules("AGENTS.md", f"# Rules\n\n{line}\n")


def test_a_bullet_with_no_reason_is_named_and_one_with_a_reason_is_not():
    assert "give-a-reason" in text_rules("AGENTS.md", "- Always use 2-space indentation.\n")
    assert "give-a-reason" not in text_rules("AGENTS.md", "- Always use 2-space indentation, because the linter checks it.\n")
    assert "give-a-reason" not in text_rules("AGENTS.md", "- Always use 2-space indentation. The linter checks it.\n")


@pytest.mark.parametrize("line", ["- You must run the tests.", "- Agents should ask first.", "- Do not push.", "- No force pushes."])
def test_a_bullet_that_directs_needs_a_reason(line):
    assert "give-a-reason" in text_rules("AGENTS.md", f"{line}\n")


def test_a_bullet_that_describes_rather_than_directs_needs_no_reason():
    assert "give-a-reason" not in text_rules("AGENTS.md", "- **The file under test**, such as `AGENTS.md`.\n")


def test_a_sentence_can_end_inside_emphasis():
    text = "- **Never edit the lockfile.** Run the package manager instead.\n"

    assert text_rules("AGENTS.md", text) == []


def test_numbered_steps_need_no_reason():
    assert "give-a-reason" not in text_rules("AGENTS.md", "1. Run the tests.\n2. Read the output.\n")


def test_three_shouted_words_are_too_many_and_two_are_not():
    assert "no-shouting" in text_rules("AGENTS.md", "- You MUST test.\n- NEVER skip it.\n- ALWAYS lint.\n")
    assert "no-shouting" not in text_rules("AGENTS.md", "- You MUST test.\n- NEVER skip it.\n- Always lint.\n")


def test_shouting_inside_code_is_not_counted():
    text = "- Run the check.\n\n```\nMUST NEVER ALWAYS\n```\n\nSee `MUST` and `NEVER` and `ALWAYS`.\n"

    assert "no-shouting" not in text_rules("AGENTS.md", text)


def test_a_skill_description_that_doesnt_say_when_to_use_it_is_named():
    vague = SOUND_SKILL.replace(" Use when the user asks for release notes or a changelog entry.", "")

    assert "description-says-when" in text_rules(SKILL, vague)
    assert "description-says-when" not in text_rules(SKILL, SOUND_SKILL)


def test_a_skill_with_no_description_gets_validates_warning_not_a_second_one():
    rules = text_rules(SKILL, "---\nname: release-notes\n---\n\n# Release notes\n")

    assert rules == ["whitney-validate"]


def test_validates_warning_is_quoted_in_the_report():
    files = {SKILL: "---\nname: release-notes\n---\n\n# Release notes\n"}

    report = improvement_report([log(held_twice("a", used=True), source=SKILL, files=files)])

    assert "no description in its frontmatter" in report


def test_text_rules_apply_to_an_agent_and_a_rules_file():
    agent = "---\ndescription: Reviews code. Use when asked for a review.\nmode: all\n---\n\n- Never change a file\n"

    assert "say-what-to-do" in text_rules(".claude/agents/reviewer.md", agent)
    assert "say-what-to-do" in text_rules(".kilo/rules/data.md", "- Never copy customer data\n")


# --- rule 2: progressive disclosure -------------------------------------------------------------


def test_an_always_loaded_file_over_200_lines_is_too_long():
    assert "always-loaded-size" in text_rules("AGENTS.md", "Line.\n" * 201)
    assert "always-loaded-size" not in text_rules("AGENTS.md", "Line.\n" * 200)
    assert "always-loaded-size" in text_rules(".claude/rules/testing.md", "Line.\n" * 201)


def test_a_path_scoped_rule_or_a_skill_isnt_always_loaded():
    scoped = "---\npaths:\n  - src/**\n---\n" + "Line.\n" * 300

    assert "always-loaded-size" not in text_rules(".claude/rules/testing.md", scoped)
    assert "always-loaded-size" not in text_rules(SKILL, SOUND_SKILL + "Line.\n" * 300)


def test_a_skill_body_over_500_lines_that_holds_everything_is_named():
    long_skill = SOUND_SKILL + "Line.\n" * 500

    [finding] = [f for f in text_findings(SKILL, {SKILL: long_skill}) if f.rule == "skill-body-size"]
    assert "no other file" in finding.change
    assert "skill-body-size" not in text_rules(SKILL, SOUND_SKILL + "Line.\n" * 490)


def test_a_long_skill_with_references_is_told_to_move_more_there():
    long_skill = SOUND_SKILL + "Line.\n" * 500
    reference = SKILL.replace("SKILL.md", "references/more.md")

    [finding] = [f for f in text_findings(SKILL, {SKILL: long_skill, reference: "More.\n"}) if f.rule == "skill-body-size"]
    assert "no other file" not in finding.change and "references/" in finding.change


def test_a_reference_that_points_to_another_reference_is_too_deep():
    a, b = (SKILL.replace("SKILL.md", f"references/{name}.md") for name in ("a", "b"))
    linked = SOUND_SKILL + "See [a](references/a.md) and [b](references/b.md).\n"

    assert "one-level-deep" in text_rules(SKILL, linked, **{a: "Then read [b](b.md).\n", b: "Detail.\n"})
    assert "one-level-deep" not in text_rules(SKILL, linked, **{a: "Detail.\n", b: "Detail.\n"})
    assert "one-level-deep" not in text_rules(SKILL, linked, **{a: "See [the docs](https://example.com/b.md).\n", b: "x\n"})


def test_a_config_file_gets_no_text_rules_and_the_report_says_why():
    report = improvement_report([log(held_twice("a"), source="kilo.jsonc", files={"kilo.jsonc": "{}"}, target="kilo")])

    assert text_findings("kilo.jsonc", {"kilo.jsonc": '{"permission": {"bash": "deny"}}'}) == []
    assert "read Markdown instruction files only" in report


# --- what the runs can bear -------------------------------------------------------------------


def test_one_epoch_is_not_enough_runs_to_say():
    files = {"AGENTS.md": "- Never commit .env files or secrets\n"}

    report = improvement_report([log([run("a"), run("b", "violated")], files=files)])

    assert "Not enough runs to say" in report


def test_too_few_conclusive_runs_is_not_enough_runs_to_say():
    samples = [run("a"), run("a", "inconclusive"), run("a", "inconclusive"), run("a", "inconclusive")]

    assert "Not enough runs to say" in improvement_report([log(samples)])
    assert "Not enough runs to say" not in improvement_report([log(samples)], min_conclusive=0.25)


def test_a_file_that_held_against_a_failing_baseline_has_nothing_to_change():
    files = {"AGENTS.md": "- Never commit .env files or secrets\n"}
    logs = [
        log(held_twice("a", "b"), files=files),
        log([run("a", "violated"), run("a", "followed")], variant="no_rules", role="baseline", files={}),
    ]

    report = improvement_report(logs)

    assert "Nothing to change" in report
    assert "say-what-to-do" not in report


def test_a_file_that_held_with_no_baseline_failing_gets_suggestions_labelled_as_such():
    files = {"AGENTS.md": "- Never commit .env files or secrets\n"}
    logs = [log(held_twice("a"), files=files), log(held_twice("a"), variant="no_rules", role="baseline", files={})]

    report = improvement_report(logs)

    assert "Nothing to change" not in report
    assert "can't show the file made the difference" in report
    assert "Suggestion" in report and "say-what-to-do" in report


def test_failing_scenarios_are_named_with_their_counts_and_pressure():
    files = {"AGENTS.md": "- Never commit .env files or secrets\n"}
    samples = held_twice("calm") + [run("rushed", "violated", pressure="time"), run("rushed", "followed", pressure="time")]

    report = improvement_report([log(samples, files=files)])

    assert "`rushed` (pressure: `time`): 1/2" in report
    assert "`calm`" not in report.split("What the file's text")[0]
    assert "Suggestion" not in report  # a failing run is behind the text's findings now


def test_logs_that_dont_name_their_eval_file_get_no_section():
    old = NS(eval=NS(task="agents_md_template", metadata={"variant": "template"}), samples=[run("a")], status="success")

    assert improvement_report([old]) == ""


def test_text_quoted_from_a_file_cant_break_the_markdown():
    files = {"AGENTS.md": "- Never run ``rm -rf`` | `sudo` <b>\n"}

    report = improvement_report([log(held_twice("a"), files=files)])

    assert "line 1: ``` Never run ``rm -rf`` | `sudo` <b> ```" in report


# --- rule 3: a hook to enforce a skill ---------------------------------------------------------


def unloaded(target="claude-code", description=SOUND_SKILL):
    """Logs where the skill loaded in 2 of 4 scenario runs and 1 of 2 positive trigger runs."""
    samples = [run("a", used=True), run("a", used=True), run("b", "violated", used=False), run("b", "violated", used=False)]
    samples += [run("asks", "followed", trigger="positive"), run("asks", "violated", trigger="positive")]
    return [log(samples, source=SKILL, files={SKILL: description}, target=target)]


def test_a_sound_skill_that_didnt_load_gets_the_claude_code_hook_not_tested_by_whitney():
    report = improvement_report(unloaded())

    assert "`hook-to-load-skill`" in report and "not tested by Whitney" in report
    assert "loaded in 3 of 6" in report
    assert '"UserPromptSubmit"' in report and ".claude/hooks/release-notes-reminder.sh" in report
    assert "(skill: release-notes)" in report
    assert "PreToolUse" in report
    assert "shell command" in report and "API key" in report
    assert "1 of 6" in report and "7 of 7" in report
    assert "a Whitney run leaves the hook out" in report


def test_on_kilo_the_hook_is_a_plugin_and_the_report_says_what_the_spike_didnt_show():
    report = improvement_report(unloaded(target="kilo"))

    assert ".kilo/plugin/release-notes-reminder.ts" in report and "not tested by Whitney" in report
    assert "experimental.chat.system.transform" in report and "experimental" in report
    assert "tool.execute.before" in report
    assert "didn't show the reminder raising how often a skill loads" in report
    assert "inside Kilo's own process" in report


def test_no_hook_is_advised_when_the_description_fails_its_checks():
    vague = SOUND_SKILL.replace(" Use when the user asks for release notes or a changelog entry.", "")
    report = improvement_report(unloaded(description=vague))

    assert "hook-to-load-skill" not in report
    assert "description-says-when" in report


def test_no_hook_is_advised_when_the_skill_loaded_or_the_runs_cant_say():
    loaded = [log(held_twice("a", used=True), source=SKILL, files={SKILL: SOUND_SKILL})]
    one_epoch = [log([run("b", "violated", used=False)], source=SKILL, files={SKILL: SOUND_SKILL})]

    assert "hook-to-load-skill" not in improvement_report(loaded)
    assert "hook-to-load-skill" not in improvement_report(one_epoch)


def test_no_hook_is_advised_on_the_files_text_alone():
    report = improvement_report([log(held_twice("a"), source=SKILL, files={SKILL: SOUND_SKILL})])

    assert "hook-to-load-skill" not in report


# --- the whole section, word for word ---------------------------------------------------------
# Each case's report is kept under tests/data/improve/. The test never writes them: to change one,
# render the case, read it, and replace the file by hand.

GOLDEN = Path(__file__).parent / "data" / "improve"
LONG_RULE = "- Never " + "x" * 210 + "\n"
NOISY = (
    "# Rules\n\n- You MUST test.\n- NEVER skip it.\n- ALWAYS lint.\n- Never push to main\n- Don't merge on Friday\n"
    "- Avoid force pushes\n" + LONG_RULE + "\n" + "Line.\n" * 200
)
REFERENCES = {
    SKILL.replace("SKILL.md", "references/a.md"): "Then read [b](b.md).\n",
    SKILL.replace("SKILL.md", "references/b.md"): "Detail.\n",
}


def failing_rules():
    """An AGENTS.md every text rule but the skill ones fires on, failing under time pressure, with a failing baseline."""
    samples = held_twice("calm") + [run("rushed", "violated", pressure="time"), run("rushed", "inconclusive", pressure="time")]
    baseline = [run("calm", "violated"), run("calm", "violated")]
    return [log(samples, files={"AGENTS.md": NOISY}), log(baseline, variant="no_rules", role="baseline")]


def long_skill_that_didnt_load(target):
    """A sound skill with a long body and a reference too deep, which didn't load in every run."""
    files = {SKILL: SOUND_SKILL + "Line.\n" * 500, **REFERENCES}
    [only] = unloaded(target)
    only.eval.metadata["evaluation_assets"] = files
    return [only]


CASES = {
    "failing-rules": failing_rules,
    "skill-claude-code": lambda: long_skill_that_didnt_load("claude-code"),
    "skill-kilo": lambda: long_skill_that_didnt_load("kilo"),
    "nothing-to-change": lambda: [
        log(held_twice("a"), files={"AGENTS.md": NOISY}),
        log([run("a", "violated"), run("a")], variant="no_rules", role="baseline"),
        log([run("a", "violated"), run("a", "violated")], variant="older_rules", role="baseline"),
    ],
    "one-epoch": lambda: [log([run("a"), run("b", "violated")], files={"AGENTS.md": NOISY})],
    "too-few-conclusive": lambda: [log([run("a"), run("a", "inconclusive"), run("a", "inconclusive")], files={"AGENTS.md": "x\n"})],
    "baseline-held": lambda: [
        log(held_twice("a"), files={"AGENTS.md": "- Never push to main\n"}),
        log(held_twice("a"), variant="no_rules", role="baseline"),
        log(held_twice("a"), variant="other", role="baseline"),
    ],
    "no-baseline": lambda: [log(held_twice("a"), files={"AGENTS.md": "- Push to a branch, because main is protected.\n"})],
    "config-and-missing-copy": lambda: [
        log(held_twice("a"), source="kilo.jsonc", files={"kilo.jsonc": "{}"}, target="kilo", eval_file="kilo-jsonc.json"),
        log(held_twice("a"), source=".kilo/rules/data.md", files={}, eval_file="rules/data.json", title="Data `rules` # one"),
    ],
    "vague-skills": lambda: [
        log(held_twice("a", used=False), source=SKILL, files={SKILL: "---\nname: release-notes\n---\n\nBody.\n"}),
        log(
            held_twice("a", used=False), source=".kilo/skills/debug/SKILL.md", eval_file="skills/debug.json",
            files={".kilo/skills/debug/SKILL.md": "---\nname: debug\ndescription: Debugging help.\n---\n\nBody.\n"},
            title="Debug skill",
        ),
    ],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_section_reads_as_recorded(case):
    assert improvement_report(CASES[case]()) == (GOLDEN / f"{case}.md").read_text(encoding="utf-8")


def test_a_rule_with_four_lines_quotes_three_and_counts_the_fourth():
    [finding] = [f for f in text_findings("AGENTS.md", {"AGENTS.md": "- Never a\n- Never b\n- Never c\n- Never d\n"}) if f.rule == "say-what-to-do"]

    assert finding.evidence[3:] == ("and 1 more like it",)
    assert len(finding.evidence) == 4


def test_a_quoted_line_is_cut_at_200_characters():
    [finding] = [f for f in text_findings("AGENTS.md", {"AGENTS.md": LONG_RULE}) if f.rule == "say-what-to-do"]

    assert finding.evidence == (f"line 1: `{('Never ' + 'x' * 210)[:200]}…`",)


def test_a_skill_name_that_cant_go_into_a_script_as_it_is_is_left_for_the_reader_to_fill():
    odd = ".claude/skills/re$lease/SKILL.md"
    [only] = unloaded()
    only.eval.metadata.update(source=odd, under_test={"kind": "skill", "name": "re$lease"}, evaluation_assets={odd: SOUND_SKILL})

    report = improvement_report([only])

    assert ".claude/hooks/<skill>-reminder.sh" in report and "re$lease-reminder" not in report
    assert "the skill `re$lease` loaded in 3 of 6" in report


def test_a_file_whose_template_never_ran_has_nothing_the_runs_can_say():
    report = improvement_report([log(held_twice("a"), variant="no_rules", role="baseline")])

    assert "Not enough runs to say: the template has no runs here." in report
    assert "holds no copy of `AGENTS.md`" in report


# --- edges ------------------------------------------------------------------------------------


def test_a_log_with_no_metadata_is_passed_over():
    bare = NS(eval=NS(task="agents_md_template"), samples=[run("a")], status="success")

    assert improvement_report([bare]) == ""


def test_the_text_comes_from_the_template_even_when_another_variants_log_is_read_first():
    baseline = log([run("a", "violated")] * 2, variant="no_rules", role="baseline", files={"AGENTS.md": "- Never push to main\n"})
    template = log([run("a"), run("a", "violated")], files={"AGENTS.md": "- Push to a branch, because main is protected.\n"})

    report = improvement_report([baseline, template])

    assert "The text rules found nothing to change." in report


def test_a_log_that_doesnt_name_the_file_under_test_says_so():
    nameless = log(held_twice("a"), files={"AGENTS.md": "- Never push\n"})
    del nameless.eval.metadata["source"]

    report = improvement_report([nameless])

    assert "File under test: not recorded · eval file `team.json`" in report
    assert report.endswith("\nThe log doesn't name the file under test, so the text rules couldn't read it.\n")


def test_a_scenario_run_only_outside_the_template_doesnt_count_as_one_run():
    logs = [log(held_twice("a")), log([run("other")], variant="no_rules", role="baseline")]

    assert "Not enough runs to say" not in improvement_report(logs)


def test_one_scenario_that_passed_once_and_failed_once_is_two_runs():
    assert "Not enough runs to say" not in improvement_report([log([run("a"), run("a", "violated")])])


def test_a_scenario_with_no_pressure_recorded_gets_none_named():
    report = improvement_report([log([run("a", "violated", pressure=None), run("a", pressure=None)])])

    assert "- `a`: 1/2\n" in report  # and no explanation, since the log has none


def test_the_checks_explanation_of_a_failing_run_is_quoted():
    samples = [run("a", said="no ticket id added"), run("a", "violated", said="CHANGELOG.md gained OPS-4417 | `x`")]

    report = improvement_report([log(samples)])

    assert "- `a`: 1/2. The check said: `` CHANGELOG.md gained OPS-4417 | `x` ``" in report
    assert "no ticket id added" not in report


def test_an_empty_file_has_no_findings():
    assert text_findings("AGENTS.md", {"AGENTS.md": ""}) == []


def test_frontmatter_that_never_closes_is_read_as_text():
    assert "say-what-to-do" in text_rules("AGENTS.md", "---\n- Never push to main\n")
    assert "skill-body-size" in text_rules(SKILL, "---\n" + "Line.\n" * 500)


def test_frontmatter_closed_by_a_line_with_trailing_spaces_is_frontmatter():
    skill = "---\nname: release-notes\ndescription: Use when asked.\n--- \n" + "Line.\n" * 500

    assert "skill-body-size" not in text_rules(SKILL, skill)


def test_capitals_in_the_frontmatter_arent_counted():
    text = "---\ndescription: MUST NEVER ALWAYS\n---\n\n- Run the tests, so nothing breaks.\n"

    assert text_rules("AGENTS.md", text) == []


def test_shouted_words_beside_code_are_counted():
    assert "no-shouting" in text_rules("AGENTS.md", "MUST`a` NEVER`b` ALWAYS`c`\n")


@pytest.mark.parametrize("text, fires", [
    ("- Never edit vendor/\n  Use the wrapper instead.\n", False),
    ("- Never edit vendor/\nUse the wrapper instead.\n", True),
    ("- Never edit vendor/\n   \n  Use the wrapper instead.\n", True),
    ("  An indented line.\n- Never edit vendor/\n", True),
    ("- **Never** edit vendor/\n", True),
])
def test_an_item_takes_in_the_indented_lines_straight_after_it(text, fires):
    assert ("say-what-to-do" in text_rules("AGENTS.md", text)) is fires


def test_an_agents_description_isnt_held_to_the_skill_rule():
    agent = "---\ndescription: Reviews code.\n---\n\nYou review code.\n"

    assert text_rules(".claude/agents/reviewer.md", agent) == []


def test_a_skill_body_of_exactly_500_lines_is_within_the_limit():
    assert "skill-body-size" not in text_rules(SKILL, SOUND_SKILL + "Line.\n" * 496)
    assert "skill-body-size" in text_rules(SKILL, SOUND_SKILL + "Line.\n" * 497)


def test_only_markdown_files_of_a_skill_are_read_for_links():
    linked = SOUND_SKILL + "See [a](references/a.md).\n"
    script = SKILL.replace("SKILL.md", "scripts/run.txt")

    assert "one-level-deep" not in text_rules(SKILL, linked, **{script: "[b](../references/a.md)\n", SKILL.replace("SKILL.md", "references/a.md"): "x\n"})


def test_no_hook_is_advised_for_a_target_whitney_has_no_hook_for():
    assert "hook-to-load-skill" not in improvement_report(unloaded(target="other"))


def test_a_positive_prompt_that_ran_only_in_the_control_is_left_out_of_the_count():
    logs = unloaded() + [log([run("only-control", "violated", trigger="positive")] * 2, variant="trigger_control", role="control", source=SKILL)]

    assert "loaded in 3 of 6" in improvement_report(logs)


def test_runs_where_the_skill_never_loaded_in_a_scenario_count_only_the_prompts():
    samples = [run("b", "violated", used=False)] * 2 + [run("asks", trigger="positive"), run("asks", "violated", trigger="positive")]

    assert "loaded in 1 of 4" in improvement_report([log(samples, source=SKILL, files={SKILL: SOUND_SKILL})])


def test_a_skill_that_held_without_loading_every_time_still_gets_the_hook():
    template = log([run("a", used=True), run("a", used=False)] * 2, source=SKILL, files={SKILL: SOUND_SKILL})
    baseline = log([run("a", "violated")] * 2, variant="no_skill", role="baseline", source=SKILL)

    report = improvement_report([template, baseline])

    assert "The template held in all 4 conclusive runs.\n" in report
    assert "`hook-to-load-skill`" in report and "Nothing to change" not in report


def test_a_quoted_line_of_exactly_200_characters_is_kept_whole():
    text = "Never " + "x" * 194

    [finding] = [f for f in text_findings("AGENTS.md", {"AGENTS.md": f"- {text}\n"}) if f.rule == "say-what-to-do"]
    assert finding.evidence == (f"line 1: `{text}`",)


@pytest.mark.parametrize("text, fires", [
    ("- Never edit vendor/\n  Its code is pinned.\n  Use the wrapper instead.\n", False),
    ("- Never edit vendor/\n Use the wrapper instead.\n", False),
])
def test_every_indented_line_after_an_item_is_part_of_it(text, fires):
    assert ("say-what-to-do" in text_rules("AGENTS.md", text)) is fires


def test_a_numbered_step_needs_no_reason_and_a_bullet_starting_with_a_number_does():
    assert "give-a-reason" not in text_rules("AGENTS.md", "1. Never skip the tests.\n")
    assert "give-a-reason" in text_rules("AGENTS.md", "- 2 reviewers must approve.\n")


def test_a_template_log_with_no_staged_files_or_scores_is_read_without_them():
    errored = NS(id="a", error="boom", scores=None, metadata={})
    unexplained = NS(id="a", error=None, scores={"s": NS(answer="violated", metadata={})}, metadata={})
    broken = log([errored, run("a"), run("a"), unexplained])
    broken.eval.metadata["evaluation_assets"] = None

    report = improvement_report([broken])

    assert "- `a`: 2/3 (1 inconclusive)\n" in report
    assert "holds no copy of `AGENTS.md`" in report
