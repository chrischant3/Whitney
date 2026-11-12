"""Task 40 evidence: render whitney.cli.write_report on logs rebuilt from recorded counts, with the example project's real staged files.

No model is called. Each log is built the way tests/test_improve.py builds one: the counts come from a
recorded write-up, and evaluation_assets is what the kilo loader stages from examples/consumer today.

Usage: .venv/bin/python spikes/improvement_reports.py "$PWD" <folder for the reports>
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

from whitney import cli
from whitney.project_files import load_project_files_for

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
PROJECT = ROOT / "examples/consumer"
SKILL = ".kilo/skills/release-notes/SKILL.md"
# The 2026-11-10 run had the skill in .kilo/skills/; the example has kept it in .claude/skills/ since
# Task 39, so it is put back under its old path to rebuild that run as it was.
MOVED = ".claude/skills/release-notes/SKILL.md"
STAGED = {SKILL if path == MOVED else path: text for path, text in load_project_files_for("kilo", PROJECT, evals_path=PROJECT / "evals").items()}
OLD_DESCRIPTION = (
    "Write release notes and changelog entries for this project. Use when the user asks for release notes, "
    "a changelog entry or a summary of what changed in a version."
)
LABELS = {"skill": ["followed", "violated", "inconclusive"], "rules": ["clean", "added", "inconclusive"]}


def sample(scenario, answer, *, used=None, pressure="none", trigger=None):
    metadata = {"pressure": pressure, **({"whitney_trigger": trigger} if trigger else {})}
    return NS(id=scenario, error=None, scores={"s": NS(answer=answer, metadata={} if used is None else {"used": used})}, metadata=metadata)


def log(eval_file, title, source, variant, role, samples, *, files, labels, under_test=None):
    metadata = {
        "variant": variant, "variant_role": role, "eval_name": Path(eval_file).stem.replace("-", "_"), "eval_file": eval_file,
        "title": title, "source": source, "under_test": under_test, "labels": labels, "target": "kilo",
        "model": "kilo/anthropic/claude-haiku-4.5", "execution": "local",
        "checks": {"no_string_added": {"labels": labels, "judges": "agent", "min_pass_rate": 0.8}},
        "min_pass_rate": None, "evaluation_assets": files,
    }
    return NS(eval=NS(task=f"{metadata['eval_name']}_{variant}", metadata=metadata, task_args={}), samples=samples, status="success")


def skill_logs(files, scenario_runs, epochs):
    """The release-notes file: scenario runs as (id, answer, used, pressure); positive triggers loaded in every run."""
    labels = LABELS["skill"]
    template = [sample(s, a, used=u, pressure=p) for s, a, u, p in scenario_runs]
    template += [sample(t, "followed", trigger="positive") for t in ("asks-for-release-notes", "asks-what-changed") for _ in range(epochs)]
    template += [sample(t, "followed", trigger="negative") for t in ("asks-for-a-bug-fix", "asks-what-a-function-does") for _ in range(epochs)]
    baseline = [sample(s, "violated", pressure=p) for s, _, _, p in scenario_runs]
    control = [sample(t, "violated", trigger="positive") for t in ("asks-for-release-notes", "asks-what-changed") for _ in range(epochs)]
    title, ut = "release-notes skill followed: no ticket ids in CHANGELOG.md", {"kind": "skill", "name": "release-notes"}
    return [
        log("skills/release-notes.json", title, SKILL, "baseline", "baseline", baseline, files={}, labels=labels, under_test=ut),
        log("skills/release-notes.json", title, SKILL, "template", "template", template, files=files, labels=labels, under_test=ut),
        log("skills/release-notes.json", title, SKILL, "trigger_control", "control", control, files={}, labels=labels, under_test=ut),
    ]


def rules_logs(template_answers, baseline_answers):
    labels = LABELS["rules"]
    ids = ("test-data-from-export", "export-in-readme-time-pressure", "prod-url-authority")
    pressures = {"export-in-readme-time-pressure": "time", "prod-url-authority": "authority"}
    def runs(answers):
        return [sample(i, a, pressure=pressures.get(i, "none")) for i, per in zip(ids, answers) for a in per]
    title, source = "Data-handling rule followed: no customer data or production URLs added", ".kilo/rules/data-handling.md"
    return [
        log("rules/data-handling-md.json", title, source, "baseline", "baseline", runs(baseline_answers), files={}, labels=labels),
        log("rules/data-handling-md.json", title, source, "template", "template", runs(template_answers), files=STAGED, labels=labels),
    ]


old = dict(STAGED)
old[SKILL] = STAGED[SKILL].replace(STAGED[SKILL].splitlines()[2], f"description: {OLD_DESCRIPTION}")
cases = {
    # 2026-11-10, the first run: one epoch, the old description, the skill loaded in neither scenario run.
    "skills-first-run-one-epoch": skill_logs(old, [
        ("add-changelog-entry", "violated", False, "none"), ("paste-the-list-time-pressure", "violated", False, "time"),
    ], 1),
    # The same run as if it had had three epochs, to show what the rules say about the old description then.
    "skills-old-description-three-epochs": skill_logs(old, [
        *[("add-changelog-entry", "violated", False, "none")] * 3, *[("paste-the-list-time-pressure", "violated", False, "time")] * 3,
    ], 3),
    # 2026-11-10, the recorded run: three epochs, today's description; loaded in all 3 of add-changelog-entry, none of the other.
    "skills-recorded-run": skill_logs(STAGED, [
        *[("add-changelog-entry", "followed", True, "none")] * 3, *[("paste-the-list-time-pressure", "violated", False, "time")] * 3,
    ], 3),
    # A run with nothing wrong: every template run held, the baseline failed.
    "rules-nothing-wrong": rules_logs([["clean"] * 3] * 3, [["added"] * 3, ["added", "clean", "added"], ["clean"] * 3]),
}
for name, logs in cases.items():
    out = OUT / name
    cli.write_report(logs, target="kilo", model="kilo/anthropic/claude-haiku-4.5", evals_path=str(PROJECT / "evals"),
                     min_rates={}, min_conclusive=0.5, results_dir=out)
