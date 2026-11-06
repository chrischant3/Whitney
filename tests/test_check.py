import fnmatch
import subprocess
from datetime import date
from pathlib import Path
from runpy import run_path

import pytest

_check = run_path(str(Path(__file__).resolve().parents[1] / "scripts/check.py"))


def test_mutation_score_counts_timeouts_as_caught_and_leaves_out_segfaults():
    statuses = ["killed"] * 6 + ["timeout"] + ["survived"] * 2 + ["no tests"] + ["segfault"] * 5
    assert _check["mutation_score"](statuses) == pytest.approx(70.0)


def test_mutation_score_of_nothing_is_none():
    assert _check["mutation_score"]([]) is None
    assert _check["mutation_score"](["segfault"]) is None


def test_touched_functions_map_changed_lines_to_mutant_name_prefixes(tmp_path):
    source = tmp_path / "src" / "whitney" / "mod.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "def untouched():\n"
        "    return 1\n"
        "\n"
        "def touched():\n"
        "    return 2\n"
        "\n"
        "class Thing:\n"
        "    def method(self):\n"
        "        return 3\n"
    )

    prefixes = _check["touched_function_prefixes"](source, {5, 9}, src_root=tmp_path / "src")

    assert prefixes == {"whitney.mod.x_touched__mutmut_", "whitney.mod.xǁThingǁmethod__mutmut_"}


def test_functions_in_a_package_init_are_named_after_the_package(tmp_path):
    # mutmut names the mutants in whitney/checks/__init__.py whitney.checks.x_..., so a prefix
    # that kept "__init__" would match none of them and the gate would never judge those functions.
    source = tmp_path / "src" / "whitney" / "checks" / "__init__.py"
    source.parent.mkdir(parents=True)
    source.write_text("def combine():\n    return 1\n")

    prefixes = _check["touched_function_prefixes"](source, {2}, src_root=tmp_path / "src")

    assert prefixes == {"whitney.checks.x_combine__mutmut_"}


def test_ratchets_are_read_from_the_measured_table():
    text = """
## Measured, not yet enforced

| Metric | Today | Direction | Checked by |
|--------|-------|-----------|------------|
| Project coverage | 77.4% | must not fall | pytest-cov |
| Mutation score (project) | 57.7% | must not fall | mutmut |
"""
    assert _check["read_ratchets"](text) == {"Project coverage": 77.4, "Mutation score (project)": 57.7}


def test_ratchet_holds_within_tolerance_and_fails_below_it():
    holds = _check["ratchet_holds"]
    assert holds(today=77.0, recorded=77.4)  # within 0.5 points
    assert not holds(today=76.8, recorded=77.4)
    assert holds(today=80.0, recorded=77.4)


# Mutation stages: `task` mutates only the touched functions, `full` the whole suite. --------

TOUCHED = {"whitney.mod.x_touched__mutmut_", "whitney.mod.xǁThingǁmethod__mutmut_"}
RATCHETS = {"Mutation score (project)": 90.0}


def _fake_mutmut(statuses: dict[str, str], run_returncode: int = 0, run_stderr: str = ""):
    """A stand-in for `_run` that answers mutmut's run and results commands, and records the run arguments."""
    runs: list[list[str]] = []

    def run(cmd, **kwargs):
        if cmd[1:4] == ["-m", "mutmut", "run"]:
            runs.append(cmd[4:])
            return subprocess.CompletedProcess(cmd, run_returncode, "", run_stderr)
        assert cmd[1:] == ["-m", "mutmut", "results", "--all", "true"], cmd
        listing = "".join(f"    {name}: {status}\n" for name, status in statuses.items())
        return subprocess.CompletedProcess(cmd, 0, listing, "")

    return run, runs


def _checks(run, today=date(2026, 11, 20)):
    """Checks after WARN_UNTIL, so a numbered check that fails is a FAIL."""
    return _check["Checks"]("origin/main", today, run=run)


def _result(checks, starts_with):
    return next((r for r in checks.results if r[0].startswith(starts_with)), None)


def test_touched_prefixes_become_mutmut_patterns_that_select_only_those_functions():
    patterns = _check["mutmut_patterns"](TOUCHED)

    assert patterns == sorted(patterns)
    names = [
        "whitney.mod.x_touched__mutmut_1",
        "whitney.mod.x_touched__mutmut_12",
        "whitney.mod.xǁThingǁmethod__mutmut_3",
        "whitney.mod.x_touched_too__mutmut_1",  # a function whose name only starts the same
        "whitney.other.mod.x_touched__mutmut_1",  # the same name in another module
        "whitney.mod.xǁOtherǁmethod__mutmut_1",
    ]
    selected = [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in patterns)]
    assert selected == names[:3]
    assert _check["mutmut_patterns"](set()) == []


def test_task_runs_mutmut_on_the_touched_functions_only():
    run, runs = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})

    _checks(run).mutation(RATCHETS, TOUCHED, stage="task")

    assert runs == [_check["mutmut_patterns"](TOUCHED)]


def test_full_runs_the_whole_mutation_suite():
    run, runs = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})

    _checks(run).mutation(RATCHETS, TOUCHED, stage="full")

    assert runs == [[]]


def test_task_with_no_touched_functions_runs_no_mutants_but_still_reports_the_cached_score():
    run, runs = _fake_mutmut({"whitney.mod.x_other__mutmut_1": "killed"})
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    assert runs == []
    assert _result(checks, "mutation score (touched functions)")[1:] == ("pass", "no touched functions")
    assert _result(checks, "mutation score (project, cached")[1] == "pass"


def test_task_labels_the_project_score_as_cached_and_leaves_out_mutants_not_checked():
    statuses = {f"whitney.mod.x_other__mutmut_{i}": "killed" for i in range(9)}
    statuses |= {"whitney.mod.x_other__mutmut_9": "survived", "whitney.mod.x_stale__mutmut_1": "not checked"}
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    name, outcome, detail = _result(checks, "mutation score (project")
    assert name == "mutation score (project, cached; run `check.py full` to refresh) must not fall"
    assert outcome == "pass"
    assert detail == "90.0% (recorded 90.0%); 1 not checked, left out"


def test_full_judges_the_project_score_from_its_own_run_under_the_usual_name():
    run, _ = _fake_mutmut({"whitney.mod.x_other__mutmut_1": "killed"})
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="full")

    assert _result(checks, "mutation score (project")[:2] == ("mutation score (project) must not fall", "pass")


def test_a_fall_in_the_cached_project_score_still_fails_the_ratchet():
    # Every figure in the cached score is a result mutmut measured, so task must not pass a fall in it.
    run, _ = _fake_mutmut({"whitney.mod.x_other__mutmut_1": "killed", "whitney.mod.x_other__mutmut_2": "survived"})
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    assert _result(checks, "mutation score (project, cached")[1] == "FAIL"
    assert checks.exit_code() == 1


def test_task_still_judges_the_touched_functions_against_their_threshold():
    statuses = {"whitney.mod.x_touched__mutmut_1": "killed", "whitney.mod.x_touched__mutmut_2": "survived",
                "whitney.mod.xǁThingǁmethod__mutmut_1": "no tests", "whitney.mod.x_other__mutmut_1": "killed"}
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation({}, TOUCHED, stage="task")

    name, outcome, detail = _result(checks, "mutation score (touched functions)")
    assert outcome == "FAIL"
    assert detail.startswith("33.3% of 3 mutants")


def test_touched_functions_with_no_mutants_pass_rather_than_stop_the_run():
    # mutmut run <patterns> stops with an AssertionError when no mutant matches, as when a change
    # touches only a function mutmut makes no mutants of. That is nothing to judge, not a broken tool.
    run, _ = _fake_mutmut(
        {"whitney.mod.x_other__mutmut_1": "killed"},
        run_returncode=1,
        run_stderr="AssertionError: Filtered for specific mutants, but nothing matches\n",
    )
    checks = _checks(run)

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    assert _result(checks, "mutation score (touched functions)")[1:] == ("pass", "no mutants in the touched functions")
    assert checks.exit_code() == 0


def test_a_mutmut_run_that_fails_for_another_reason_is_an_error():
    run, _ = _fake_mutmut({}, run_returncode=1, run_stderr="failed to collect stats. runner returned 1\n")
    checks = _checks(run)

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    assert _result(checks, "mutation testing")[1] == "ERROR"
    assert checks.exit_code() == 2
