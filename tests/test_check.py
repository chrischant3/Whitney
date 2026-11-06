import fnmatch
import json
import subprocess
import tempfile
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


def _fake_mutmut(statuses: dict[str, str], run_returncode: int = 0, run_stderr: str = "", results_returncode: int = 0):
    """A stand-in for `_run` that answers mutmut's run and results commands, and records the run arguments."""
    runs: list[list[str]] = []

    def run(cmd, **kwargs):
        if cmd[1:4] == ["-m", "mutmut", "run"]:
            runs.append(cmd[4:])
            return subprocess.CompletedProcess(cmd, run_returncode, "", run_stderr)
        assert cmd[1:] == ["-m", "mutmut", "results", "--all", "true"], cmd
        listing = "".join(f"    {name}: {status}\n" for name, status in statuses.items())
        return subprocess.CompletedProcess(cmd, results_returncode, listing, "")

    return run, runs


def _mutants_dir(hashed: dict[str, set[str]] | None = None) -> Path:
    """A mutants/ folder whose .meta files say which functions mutmut made mutants of.

    `hashed` maps a source path under src/ to its mutated functions; by default whitney/mod.py
    with both TOUCHED functions.
    """
    root = Path(tempfile.mkdtemp())
    hashed = {"whitney/mod.py": {"x_touched", "xǁThingǁmethod"}} if hashed is None else hashed
    for source, functions in hashed.items():
        meta = root / "src" / f"{source}.meta"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"exit_code_by_key": {}, "hash_by_function_name": {f: "0" for f in functions}}))
    return root


def _checks(run, today=date(2026, 11, 20), mutants=None):
    """Checks after WARN_UNTIL, so a numbered check that fails is a FAIL."""
    return _check["Checks"]("origin/main", today, run=run, mutants=mutants or _mutants_dir())


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

    # With a name, mutmut reruns every mutant it selects instead of keeping cached verdicts,
    # and "*" also picks every test for its clean run.
    assert runs == [["*"]]


def test_task_with_no_touched_functions_runs_no_mutants_but_still_reports_the_cached_score():
    run, runs = _fake_mutmut({"whitney.mod.x_other__mutmut_1": "killed"})
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    assert runs == []
    assert _result(checks, "mutation score (touched functions)")[1:] == ("pass", "no touched functions")
    assert _result(checks, "mutation score (project, cached")[1] == "pass"


def test_task_labels_the_project_score_as_cached_and_leaves_out_mutants_not_checked():
    statuses = {f"whitney.mod.x_other__mutmut_{i}": "killed" for i in range(19)}  # 1 of 21 not checked, under 5%
    statuses |= {"whitney.mod.x_other__mutmut_19": "survived", "whitney.mod.x_stale__mutmut_1": "not checked"}
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    name, outcome, detail = _result(checks, "mutation score (project")
    assert name == "mutation score (project, cached; run `check.py full` to refresh) must not fall"
    assert outcome == "pass"
    assert detail == "95.0% (recorded 90.0%); 1 not checked, left out"


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
    checks = _checks(run, mutants=_mutants_dir({"whitney/mod.py": {"x_other"}}))

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    assert _result(checks, "mutation score (touched functions)")[1:] == (
        "pass", "no mutants in the touched functions: whitney.mod.x_touched, whitney.mod.xǁThingǁmethod"
    )
    assert checks.exit_code() == 0


def test_a_mutmut_run_that_fails_for_another_reason_is_an_error():
    run, _ = _fake_mutmut({}, run_returncode=1, run_stderr="failed to collect stats. runner returned 1\n")
    checks = _checks(run)

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    assert _result(checks, "mutation testing")[1] == "ERROR"
    assert checks.exit_code() == 2


def test_the_mutmut_patterns_also_name_the_touched_functions_so_mutmut_picks_their_tests():
    # mutmut's clean test run before the mutants runs the tests it finds by matching the patterns
    # against function names; with none it runs the whole suite, which is most of the stage's time.
    patterns = _check["mutmut_patterns"](TOUCHED)

    functions = ["whitney.mod.x_touched", "whitney.mod.xǁThingǁmethod", "whitney.mod.x_touched_too", "whitney.mod.x_other"]
    assert [f for f in functions if any(fnmatch.fnmatchcase(f, p) for p in patterns)] == functions[:2]


def test_a_cached_project_score_missing_too_many_mutants_warns_with_its_coverage():
    statuses = {f"whitney.mod.x_other__mutmut_{i}": "killed" for i in range(18)}
    statuses |= {f"whitney.mod.x_stale__mutmut_{i}": "not checked" for i in range(2)}  # 10%, over 5%
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    name, outcome, detail = _result(checks, "mutation score (project, cached")
    assert outcome == "warn"
    assert "cached score covers 18 of 20 mutants; run `check.py full`" in detail


def test_a_cached_project_score_missing_few_mutants_is_judged_as_usual():
    statuses = {f"whitney.mod.x_other__mutmut_{i}": "killed" for i in range(19)}
    statuses |= {"whitney.mod.x_stale__mutmut_1": "not checked"}  # exactly 5%
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    assert _result(checks, "mutation score (project, cached")[1] == "pass"


def test_a_fall_in_a_thin_cached_score_still_fails():
    statuses = {"whitney.mod.x_other__mutmut_1": "survived", "whitney.mod.x_stale__mutmut_1": "not checked"}
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    assert _result(checks, "mutation score (project, cached")[1] == "FAIL"


def test_task_with_no_cached_results_warns_instead_of_dropping_the_project_score():
    run, _ = _fake_mutmut({})
    checks = _checks(run)

    checks.mutation(RATCHETS, set(), stage="task")

    name, outcome, detail = _result(checks, "mutation score (project, cached")
    assert outcome == "warn"
    assert "run `check.py full`" in detail


def test_mutmut_results_failing_is_an_error():
    run, _ = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"}, results_returncode=1)
    checks = _checks(run)

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    assert _result(checks, "mutation results")[1] == "ERROR"
    assert checks.exit_code() == 2


def test_the_touched_detail_names_touched_functions_mutmut_made_no_mutants_of():
    # A decorated function, or one with nothing to mutate, has no mutants and no hash in the .meta.
    run, _ = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})
    checks = _checks(run, mutants=_mutants_dir({"whitney/mod.py": {"x_touched"}}))

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    name, outcome, detail = _result(checks, "mutation score (touched functions)")
    assert outcome == "pass"
    assert detail == "100.0% of 1 mutants; no mutants in whitney.mod.xǁThingǁmethod"


def test_a_touched_function_mutmut_mutated_but_no_prefix_matches_is_an_error():
    # mutmut hashes only the functions it made mutants of, so a hashed function with none under
    # our prefix means the prefix is wrong, and the gate would never judge that function.
    statuses = {"whitney.mod.x_touched_renamed__mutmut_1": "killed", "whitney.mod.xǁThingǁmethod__mutmut_1": "killed"}
    run, _ = _fake_mutmut(statuses)
    checks = _checks(run)

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    name, outcome, detail = _result(checks, "mutation testing")
    assert outcome == "ERROR"
    assert "whitney.mod.x_touched" in detail


def test_a_touched_module_mutmut_has_no_record_of_is_an_error():
    run, _ = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})
    checks = _checks(run, mutants=_mutants_dir({}))

    checks.mutation(RATCHETS, TOUCHED, stage="task")

    assert _result(checks, "mutation testing")[1] == "ERROR"


def test_a_package_init_is_found_by_its_package_name():
    run, _ = _fake_mutmut({"whitney.checks.x_combine__mutmut_1": "killed"})
    checks = _checks(run, mutants=_mutants_dir({"whitney/checks/__init__.py": {"x_combine"}}))

    checks.mutation(RATCHETS, {"whitney.checks.x_combine__mutmut_"}, stage="task")

    assert _result(checks, "mutation score (touched functions)")[1:] == ("pass", "100.0% of 1 mutants")


def test_full_finding_no_mutants_at_all_is_an_error():
    run, _ = _fake_mutmut({}, run_returncode=1, run_stderr="AssertionError: Filtered for specific mutants, but nothing matches\n")
    checks = _checks(run)

    checks.mutation(RATCHETS, TOUCHED, stage="full")

    assert _result(checks, "mutation testing")[1] == "ERROR"
    assert checks.exit_code() == 2


def test_a_package_init_prefix_becomes_patterns_for_its_mutants_and_its_function():
    patterns = _check["mutmut_patterns"]({"whitney.checks.x_combine__mutmut_"})

    names = ["whitney.checks.x_combine__mutmut_1", "whitney.checks.__init__.x_combine__mutmut_1", "whitney.checks.x_combine"]
    assert [n for n in names if any(fnmatch.fnmatchcase(n, p) for p in patterns)] == [names[0], names[2]]


class _Distribution:
    """Enough of importlib.metadata.Distribution for installed_requirements."""

    def __init__(self, name, version, direct_url=None):
        self.metadata = {"Name": name}
        self.version = version
        self._direct_url = direct_url

    def read_text(self, filename):
        return self._direct_url if filename == "direct_url.json" else None


def test_installed_requirements_pin_every_distribution_but_editables():
    distributions = [
        _Distribution("requests", "2.32.3"),
        _Distribution("whitney", "0.1.0", json.dumps({"url": "file:///x", "dir_info": {"editable": True}})),
        _Distribution("wheelhouse", "1.0", json.dumps({"url": "file:///y.whl", "archive_info": {}})),
        _Distribution("Requests", "2.32.3"),  # the same distribution found twice on the path
    ]

    assert _check["installed_requirements"](distributions) == ["requests==2.32.3", "wheelhouse==1.0"]


def test_an_empty_package_list_is_an_error_not_a_clean_scan():
    scanned = []
    checks = _check["Checks"]("origin/main", date(2026, 11, 20), run=lambda cmd, **kw: scanned.append(cmd))

    checks.dependencies(distributions=[])

    assert _result(checks, "dependency vulnerabilities")[1] == "ERROR"
    assert scanned == []


def test_a_distribution_installed_editable_is_left_out_with_every_other_copy_of_it():
    # An editable install also leaves src/<name>.egg-info on the path, which records no direct_url.
    distributions = [
        _Distribution("whitney", "0.1.0", json.dumps({"url": "file:///x", "dir_info": {"editable": True}})),
        _Distribution("whitney", "0.1.0"),
        _Distribution("requests", "2.32.3"),
    ]

    assert _check["installed_requirements"](distributions) == ["requests==2.32.3"]
