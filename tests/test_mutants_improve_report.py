"""Edges of the improvement report, the Markdown report and the JUnit XML that the full mutation run showed untested (Later plan Task 65).

Each test pins behaviour a reader of the report sees; the mutant it was written
against is named in its docstring.
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from types import SimpleNamespace as NS

from whitney.improve import improvement_report, text_findings
from whitney.junit import junit_xml
from whitney.report import records_from_log, render_header, render_markdown, summarise

LABELS = ["followed", "violated", "inconclusive"]
SKILL = ".claude/skills/release-notes/SKILL.md"
SOUND_SKILL = (
    "---\nname: release-notes\n"
    "description: Write release notes. Use when the user asks for release notes or a changelog entry.\n"
    "---\n\n# Release notes\n\n1. Read the merged changes.\n"
)


def run(scenario, answer="followed", *, used=None, trigger=None, said=None, metadata=None):
    """One sample, as tests/test_improve.py builds them."""
    sample_metadata = {"pressure": "none", **({"whitney_trigger": trigger} if trigger else {}), **(metadata or {})}
    score = NS(answer=answer, metadata={} if used is None else {"used": used}, explanation=said)
    return NS(id=scenario, error=None, scores={"s": score}, metadata=sample_metadata)


def log(samples, *, variant="template", role="template", source="AGENTS.md", files=None, target="claude-code", **extra):
    """A log as scenario_eval writes it, for one variant of team.json."""
    under_test = {"kind": "skill", "name": source.split("/")[-2]} if source.endswith("SKILL.md") else None
    metadata = {
        "variant": variant, "variant_role": role, "eval_name": "team_rules", "eval_file": "team.json",
        "title": "Team rule", "source": source, "under_test": under_test, "labels": LABELS, "target": target,
        "checks": {"no_secret_committed": {"labels": LABELS, "judges": "agent", "min_pass_rate": 0.8}},
        "min_pass_rate": None, "evaluation_assets": dict(files or {}), **extra,
    }
    return NS(eval=NS(task=f"team_rules_{variant}", metadata=metadata, task_args={}), samples=list(samples), status="success")


def unloaded():
    """The skill loaded in 2 of 4 scenario runs and 1 of 2 runs of the positive prompt `asks`."""
    samples = [run("a", used=True), run("a", used=True), run("b", "violated", used=False), run("b", "violated", used=False)]
    samples += [run("asks", used=True, trigger="positive"), run("asks", "violated", used=False, trigger="positive")]
    return samples


def rendered(logs) -> str:
    return render_markdown(summarise([record for entry in logs for record in records_from_log(entry)]))


# --- whitney.improve ------------------------------------------------------------------------


def test_a_template_run_with_no_score_doesnt_stop_the_section():
    """_templates: a sample whose scores are missing gives no explanation, and the rest are still read."""
    unscored = NS(id="a", error=None, scores=None, metadata={"pressure": "none"})
    samples = [run("a"), run("a", "violated", said="the env file went in"), unscored]

    report = improvement_report([log(samples)])

    assert "- `a`: 1/2 (1 inconclusive). The check said: `the env file went in`" in report


def test_runs_too_few_to_say_get_no_hook_even_when_every_run_missed():
    """_section: with not enough runs to say, the runs' own findings aren't worked out."""
    samples = [run(name, "violated", used=False) for name in ("a", "b", "c", "d")]

    report = improvement_report([log(samples, source=SKILL, files={SKILL: SOUND_SKILL})])

    assert "Not enough runs to say: each scenario ran once" in report
    assert "hook-to-load-skill" not in report


def test_a_home_folder_ending_in_a_slash_is_still_shown_as_a_tilde(monkeypatch):
    """_home_as_tilde: the home folder's trailing slash, and only it, is dropped before matching."""
    monkeypatch.setenv("HOME", "/home/MAX/")
    samples = [run("a"), run("a", "violated", said="wrote /home/MAX/app/.env")]

    assert "The check said: `wrote ~/app/.env`" in improvement_report([log(samples)])


def test_a_home_of_the_root_folder_hides_nothing(monkeypatch):
    """_home_as_tilde: a home of "/" (HOME empty or "/") is no folder to hide, so no slash becomes a tilde."""
    for home in ("", "/"):
        monkeypatch.setenv("HOME", home)
        samples = [run("a"), run("a", "violated", said="moved a / b to app/.env")]

        assert "The check said: `moved a / b to app/.env`" in improvement_report([log(samples)]), home


def test_a_line_that_continues_an_item_counts_for_every_item_it_sits_under():
    """_items: a continuation of the deepest item is part of the text nested in each item above it."""
    text = "- Rules:\n  - Always run the tests\n    - and lint\n      because the build checks both.\n"

    assert "give-a-reason" not in [f.rule for f in text_findings("AGENTS.md", {"AGENTS.md": text})]


def test_a_reason_straight_after_a_label_is_read():
    """_no_reason: the label's colon is taken out, not replaced, so a reason after it still starts a word."""
    text = "- Never:because main deploys on every push\n"

    assert "give-a-reason" not in [f.rule for f in text_findings("AGENTS.md", {"AGENTS.md": text})]


def test_validates_warning_for_this_skill_is_found_after_one_for_another():
    """_validate_warnings: a warning about another skill is passed over, not the end of the search."""
    first, later = ".claude/skills/aaa/SKILL.md", ".claude/skills/bbb/SKILL.md"
    no_description = "---\nname: x\n---\n\n# X\n"
    files = {first: no_description, later: no_description}

    assert "whitney-validate" in [f.rule for f in text_findings(later, files)]


def test_the_hook_counts_a_positive_prompt_that_loaded_and_names_every_positive_prompt():
    """_hook_findings and _could_tell: a positive prompt that loaded counts as loaded, and is still one to retry."""
    samples = unloaded() + [run("asks2", used=True, trigger="positive")] * 2
    baseline = log([run("a", "violated", used=False)], variant="no_rules", role="baseline", source=SKILL)

    report = improvement_report([baseline, log(samples, source=SKILL, files={SKILL: SOUND_SKILL})])

    assert "the skill `release-notes` loaded in 5 of 8 runs that could tell" in report
    assert "Add the hook, then run `asks`, `asks2`, `b` in Claude Code itself," in report


# --- whitney.report -------------------------------------------------------------------------


def refused_log(said, calls=1):
    sample = NS(id="s", error=None, metadata={}, scores={
        "x": NS(answer="followed", metadata={"refusals": [{"rule": "Skill(x)", "calls": calls, "said": said}]}),
    })
    return log([sample])


def test_a_refusal_whose_words_arent_text_is_shown_with_none():
    """_refusals and _refused_note: words that aren't a string are dropped, and none show as an empty quote."""
    for said in (7, None):
        assert 'The harness refused 1 call to it, in 1 of 1 run: ""\n' in rendered([refused_log(said)])


def test_a_refusal_of_exactly_the_limit_is_kept_whole():
    """_refused_note: words of exactly SAID_LIMIT characters are shown whole, with no ellipsis."""
    said = "x" * 200

    assert f': "{said}"\n' in rendered([refused_log(said)])


def test_a_scenario_that_is_a_known_gap_in_one_log_only_counts_its_other_runs():
    """render_markdown: a file with no trigger tests shows the overall its runs counted, run by run."""
    gap = {"known_gap": "E3: the harness can't tell"}
    logs = [log([run("a", metadata=gap), run("b")]), log([run("a", "violated")], variant="no_rules", role="baseline")]

    assert "| **overall** | 0/1 | 1/1 | +100% |" in rendered(logs)


def header_log(log_version, *score_versions, samples=True):
    metadata = {
        "variant": "template", "variant_role": "template", "eval_name": "team_rules", "eval_file": "team.json",
        "title": "Team rule", "labels": LABELS, "min_pass_rate": None, "whitney_version": "0.2.0",
        "checks": {"no_secret_committed": {"labels": LABELS, "judges": "agent", "min_pass_rate": 0.8}},
        "harness_version": log_version,
    }
    entry = NS(eval=NS(task="x", metadata=metadata))
    if samples:
        entry.samples = [
            NS(id="s", error=None, metadata={}, scores={"x": NS(answer="followed", metadata={"harness_version": version})})
            for version in score_versions
        ]
    return entry


def test_a_harness_version_the_log_recorded_is_the_only_one_shown():
    """render_header: a log's own harness version stands; its scores' versions are read only when it has none."""
    line = render_header([header_log("2.1.322-host", "2.1.400 (container)")]).splitlines()[0]

    assert line == "Whitney: `0.2.0` · Harness: `2.1.322-host` · Evals: not given"


def test_a_log_or_a_sample_without_scores_recorded_has_no_harness_version():
    """_container_versions: a log with no samples, and a sample with no scores, are passed over."""
    no_scores = header_log(None)
    no_scores.samples = [NS(id="s", error=None, metadata={})]

    line = render_header([header_log(None, samples=False), no_scores]).splitlines()[0]

    assert line == "Whitney: `0.2.0` · Harness: not recorded · Evals: not given"


# --- whitney.junit --------------------------------------------------------------------------


def test_a_score_with_no_metadata_falls_back_to_the_runs_outcome():
    """_outcomes: a score object with no metadata attribute is judged by the run's own outcome."""
    score = NS(answer="violated", explanation="it went in")
    sample = NS(id="a", epoch=1, error=None, scores={"s": score}, metadata={})
    entry = log([sample], target="kilo", model="m/x", execution="local")

    text = junit_xml([entry], [], min_rates={}, min_conclusive=0.5, timestamp=datetime(2026, 11, 16, 9, 0, 0))

    suite = {s.get("name"): s for s in ET.fromstring(text).iter("testsuite")}["team.json"]
    failure = {c.get("name"): c for c in suite.iter("testcase")}["no_secret_committed"].find("failure")
    assert failure.get("message").startswith("0 of 1 conclusive")
    assert failure.text == "a, epoch 1: it went in"
