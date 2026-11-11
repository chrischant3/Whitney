"""The refusals an eval file asked for, from the scorer to the report (Phase 5 Task 36b).

An eval file can switch off a skill or an agent in its own harness_permissions.
Each run's score records what each such rule refused, and the report says so
for the file, so a reader has the harness's own words as proof.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from whitney import tasks
from whitney.checks import Evidence
from whitney.report import records_from_log, render_markdown, summarise

DATA = Path(__file__).parent / "data"
LABELS = ["unchanged", "edited", "inconclusive"]
BLOCKED = "Skill execution blocked by permission rules"


def recorded(target: str, name: str) -> dict:
    """A recorded run, as the agent stores it."""
    folder = DATA / ("claude-code-events" if target == "claude-code" else "kilo-events")
    meta = json.loads((folder / f"{name}.meta.json").read_text())
    return {
        "events": (folder / f"{name}.jsonl").read_text(), "exit_code": 0, "stderr": "",
        "permission_denials": meta.get("permission_denials") or [], "permissions": meta.get("harness_permissions"),
    }


def score_of(target: str, run: dict, permissions: dict | None, monkeypatch, secrets=()):
    """The scenario scorer's score for one run, given the eval file's permissions for the target."""

    async def collect(*args, **kwargs):
        return Evidence(exit_code=0)

    monkeypatch.setattr(tasks, "store", lambda: SimpleNamespace(get=lambda key, default=None: run))
    monkeypatch.setattr(tasks.evidence, "collect", collect)
    monkeypatch.setattr(tasks.evidence, "run_secrets", lambda *args: list(secrets))
    monkeypatch.setattr(tasks, "sandbox", lambda: None)
    score = tasks._checks_scorer(
        {"s": [("no_file_edits", {})]}, LABELS, [], target, "local", None, "python3", permissions=permissions,
    )
    return asyncio.run(score(SimpleNamespace(sample_id="s", uuid="none-s", metadata={}), None))


# --- The score ------------------------------------------------------------------------------


@pytest.mark.parametrize("target, name, expected", [
    ("claude-code", "skill-denied-builtin", {"rule": "Skill(code-review)", "calls": 2, "said": BLOCKED}),
    ("claude-code", "agent-denied", {
        "rule": "Agent(reviewer)", "calls": 1,
        "said": "Agent type 'reviewer' has been denied by permission rule 'Agent(reviewer)' from flagSettings.",
    }),
])
def test_the_score_records_what_each_rule_of_the_eval_files_refused(monkeypatch, target, name, expected):
    run = recorded(target, name)

    assert score_of(target, run, run["permissions"], monkeypatch).metadata["refusals"] == [expected]


def test_the_score_records_a_kilo_refusal_too(monkeypatch):
    run = recorded("kilo", "task-denied-overlay")

    [refusal] = score_of("kilo", run, run["permissions"], monkeypatch).metadata["refusals"]

    assert (refusal["rule"], refusal["calls"]) == ("task: general", 1)
    assert refusal["said"].startswith("The user has specified a rule which prevents you")


def test_a_rule_that_refused_nothing_is_recorded_as_such(monkeypatch):
    run = recorded("claude-code", "skill-named")
    permissions = {"allow": ["Bash"], "deny": ["Skill(code-review)"]}

    assert score_of("claude-code", run, permissions, monkeypatch).metadata["refusals"] == [
        {"rule": "Skill(code-review)", "calls": 0, "said": None},
    ]


@pytest.mark.parametrize("permissions", [None, {}, {"allow": ["Bash"], "deny": ["Bash(git push *)"]}])
def test_a_file_that_switched_nothing_off_has_no_refusals_in_its_score(monkeypatch, permissions):
    run = recorded("claude-code", "skill-denied")

    assert "refusals" not in score_of("claude-code", run, permissions, monkeypatch).metadata


def test_the_harnesss_words_are_redacted_like_the_rest_of_the_score(monkeypatch):
    run = recorded("claude-code", "agent-denied")

    [refusal] = score_of("claude-code", run, run["permissions"], monkeypatch, secrets=["flagSettings"]).metadata["refusals"]

    assert refusal["said"] == "Agent type 'reviewer' has been denied by permission rule 'Agent(reviewer)' from [redacted]."


# --- The report -----------------------------------------------------------------------------


def log(variant: str, refusals_per_run: list, role: str | None = None):
    """A log as scenario_eval writes it, one run per entry of `refusals_per_run` (None: a score that records none)."""
    metadata = {
        "variant": variant, "variant_role": role or variant, "eval_name": "agents_md", "eval_file": "agents-md.json",
        "title": "Rules", "labels": LABELS, "min_pass_rate": None, "under_test": None,
        "checks": {"no_file_edits": {"labels": LABELS, "judges": "agent", "min_pass_rate": 0.8}},
    }
    samples = [
        SimpleNamespace(id="s", error=None, metadata={}, scores={
            "x": SimpleNamespace(answer="unchanged", metadata={} if refusals is None else {"refusals": refusals}),
        })
        for refusals in refusals_per_run
    ]
    return SimpleNamespace(eval=SimpleNamespace(task="x", metadata=metadata), samples=samples)


def rendered(logs) -> str:
    return render_markdown(summarise([record for entry in logs for record in records_from_log(entry)]))


def skill(calls: int, rule: str = "Skill(code-review)") -> dict:
    return {"rule": rule, "calls": calls, "said": BLOCKED if calls else None}


def test_the_report_says_what_each_rule_refused_over_every_variants_runs():
    logs = [log("template", [[skill(2)], [skill(0)]]), log("baseline", [[skill(1)]])]

    assert (
        '\nThe eval file switched off `Skill(code-review)`. The harness refused 3 calls to it, in 2 of 3 runs: '
        '"Skill execution blocked by permission rules"\n'
    ) in rendered(logs)


def test_one_refused_call_in_one_run_is_said_in_the_singular():
    assert "The harness refused 1 call to it, in 1 of 1 run:" in rendered([log("template", [[skill(1)]])])


def test_a_rule_that_refused_nothing_is_reported_so_silence_isnt_taken_for_proof():
    logs = [log("template", [[skill(0)], [skill(0)]])]

    assert (
        "\nThe eval file switched off `Skill(code-review)`. No call was refused under it, "
        "so these 2 runs don't show the rule refusing anything.\n"
    ) in rendered(logs)
    assert "No call was refused under it, so this 1 run doesn't show the rule refusing anything." in rendered([log("template", [[skill(0)]])])


def test_each_rule_gets_its_own_line_in_the_order_the_file_gave_them():
    agent = {"rule": "Agent(reviewer)", "calls": 1, "said": "Agent type 'reviewer' has been denied."}
    text = rendered([log("template", [[skill(0), agent]])])

    assert text.index("`Skill(code-review)`") < text.index("`Agent(reviewer)`")
    assert 'The harness refused 1 call to it, in 1 of 1 run: "Agent type \'reviewer\' has been denied."' in text


def test_a_long_refusal_is_cut_and_kept_on_one_line():
    said = "The user has specified a rule\nwhich prevents you " + "x" * 400
    text = rendered([log("template", [[{"rule": "skill: kilo-config", "calls": 1, "said": said}]])])

    line = next(line for line in text.splitlines() if "`skill: kilo-config`" in line)
    assert '"The user has specified a rule which prevents you xxx' in line
    assert line.endswith('…"') and len(line) < 400


def test_a_report_of_logs_with_no_refusals_says_nothing_about_them():
    assert "switched off" not in rendered([log("template", [None, None])])


@pytest.mark.parametrize("odd", [
    "Skill(code-review)", [7], [{"rule": 7, "calls": 1, "said": "x"}], [{"rule": "Skill(a)", "calls": "many", "said": "x"}],
    [{"rule": "Skill(a)", "calls": True, "said": "x"}], [{"rule": "Skill(a)"}], [{"rule": "Skill(a)", "calls": -1, "said": "x"}],
])
def test_refusals_a_log_records_in_another_shape_are_left_out(odd):
    assert "switched off" not in rendered([log("template", [odd])])


def test_a_rule_whose_name_holds_a_newline_or_a_backtick_cant_break_the_report():
    text = rendered([log("template", [[{"rule": "skill: a\nb`c", "calls": 0, "said": None}]])])

    assert "The eval file switched off `skill: a b'c`." in text
