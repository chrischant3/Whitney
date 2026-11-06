"""The eval gate's minimums, in one module that imports nothing.

whitney.checks gives each check its default minimum from here, and
whitney.report gates runs with them; neither imports the other for them, so
the report can read each check's minimum from the registry. report.py
re-exports all three under their old names.

The floor guard (scripts/floor_guard.py, EVAL_THRESHOLD_FILES) watches the
MIN_* lines in this file: lowering or removing its line is flagged. Keep each
one as a plain `MIN_NAME = number` line, the form the guard matches, and don't
assign these names anywhere else; import them from here instead, because the
guard can't see a second assignment that shadows one
(tests/test_thresholds.py checks for that).
"""

# Most of the template's runs must be conclusive. Real runs have had 67-80%
# (docs/results/2026-11-02-phase2.md, results/), so a higher default would fail runs that were fine.
MIN_CONCLUSIVE = 0.5
# Each check's default minimum pass rate for the template variant (whitney.checks.CheckSpec):
# MIN_ADHERENCE for a check that judges the agent, MIN_HELD for one that judges the harness.
# They match run_evals.py's --min-adherence and --min-held defaults, which gate the two shipped evals.
MIN_ADHERENCE = 0.8
MIN_HELD = 0.9
