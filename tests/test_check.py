import fnmatch
import json
import subprocess
import tempfile
import tomllib
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


def test_the_test_step_runs_pytest_on_every_core_with_coverage():
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "1 failed, 9 passed in 1.00s\n", "")

    checks = _checks(run)

    assert checks.tests_and_coverage() is None
    [command] = commands
    assert command[1:3] == ["-m", "pytest"]
    assert command[command.index("-n") + 1] == "auto"
    assert {"--cov=whitney", "--cov-report=xml:coverage.xml"} <= set(command)
    assert _result(checks, "tests")[1:] == ("FAIL", "1 failed, 9 passed in 1.00s")


def test_full_runs_the_tests_that_install_the_wheel():
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "4 passed, 7 deselected in 7.00s\n", "")

    checks = _checks(run)
    checks.installed_package()

    [command] = commands
    assert command[1:3] == ["-m", "pytest"]
    assert command[command.index("-m", 2) + 1] == "packaging"  # after pyproject's `-m`, so it replaces it
    assert _result(checks, "installed package")[1:] == ("pass", "4 passed, 7 deselected in 7.00s")


def test_a_failing_install_test_fails_the_checks():
    checks = _checks(lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, "1 failed, 3 passed in 7.00s\n", ""))
    checks.installed_package()

    assert _result(checks, "installed package")[1:] == ("FAIL", "1 failed, 3 passed in 7.00s")
    assert checks.exit_code() == 1


def test_install_tests_that_find_nothing_to_run_are_an_error_not_a_pass():
    # pytest exits 5 when no test was collected: a renamed marker would otherwise pass unnoticed
    checks = _checks(lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 5, "11 deselected in 0.10s\n", ""))
    checks.installed_package()

    assert _result(checks, "installed package")[1] == "ERROR"


def test_the_default_test_run_leaves_the_install_tests_out():
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())

    assert pyproject["tool"]["pytest"]["ini_options"]["addopts"] == ["-m", "not e2e and not packaging"]


def test_mutmut_and_a_plain_pytest_run_stay_on_one_process():
    # mutmut runs its own workers, so its pytest runs must not start xdist ones too;
    # only check.py's test step asks for -n.
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    mutmut = pyproject["tool"]["mutmut"]
    arguments = [
        *pyproject["tool"]["pytest"]["ini_options"]["addopts"],
        *mutmut["pytest_add_cli_args_test_selection"],
        *mutmut.get("pytest_add_cli_args", []),
    ]

    assert not [a for a in arguments if a.startswith(("-n", "--numprocesses", "--dist"))]


def test_a_mutant_that_loops_forever_fails_its_test_in_seconds_not_minutes():
    # A mutant such as `i += 1` -> `i += 2` in a parser loop never ends, and mutmut's own limit,
    # (the covering tests' time + 1) x 15, gave such mutants up to 217 s each while their lists
    # grew to 5-7 GB, until the OOM killer took them and mutmut recorded a segfault, which the
    # score leaves out. A per-test timeout on mutmut's runs fails the looping test instead.
    # It sits well above the slowest test (about 6.5 s) so a slow machine can't fail a sound one.
    pyproject = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    arguments = pyproject["tool"]["mutmut"].get("pytest_add_cli_args", [])
    timeouts = [int(a.removeprefix("--timeout=")) for a in arguments if a.startswith("--timeout=")]

    assert timeouts == [30]
    assert any(r.startswith("pytest-timeout==") for r in pyproject["project"]["optional-dependencies"]["dev"])
    # Only mutmut's runs get it: a plain or check.py run has no per-test timeout.
    assert not [a for a in pyproject["tool"]["pytest"]["ini_options"]["addopts"] if a.startswith("--timeout")]


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


def test_a_worker_cap_reaches_mutmut_in_both_stages():
    # mutmut starts one worker per core unless told otherwise, which is too many for a laptop's memory.
    for stage, selection in (("full", ["*"]), ("task", _check["mutmut_patterns"](TOUCHED))):
        run, runs = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})

        _check["Checks"]("origin/main", date(2026, 11, 20), run=run, mutants=_mutants_dir(), max_children=6).mutation(
            RATCHETS, TOUCHED, stage=stage
        )

        assert runs == [["--max-children", "6", *selection]]


def test_without_a_worker_cap_mutmut_chooses_its_own():
    run, runs = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})

    _checks(run).mutation(RATCHETS, TOUCHED, stage="full")

    assert not [a for a in runs[0] if a.startswith("--max-children")]


def test_the_worker_cap_is_a_command_line_option_of_at_least_one():
    parse = _check["parse_args"]

    assert parse(["full", "--max-children", "6"]).max_children == 6
    assert parse(["task"]).max_children is None
    for bad in ("0", "-1", "many"):
        with pytest.raises(SystemExit):
            parse(["full", "--max-children", bad])


def test_full_rebuilds_mutmuts_map_of_which_tests_reach_which_function():
    # mutmut 3.8.1 adds a test to its map only when the test is new, so an existing test that
    # starts reaching a function would never be run against that function's mutants.
    mutants = _mutants_dir()
    (mutants / "mutmut-stats.json").write_text("{}")
    seen = []

    def run(cmd, **kw):
        if cmd[3] == "run":
            seen.append((mutants / "mutmut-stats.json").exists())
        return _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})[0](cmd, **kw)

    _checks(run, mutants=mutants).mutation(RATCHETS, TOUCHED, stage="full")

    assert seen == [False]


def test_task_keeps_mutmuts_map_of_which_tests_reach_which_function():
    mutants = _mutants_dir()
    (mutants / "mutmut-stats.json").write_text("{}")
    run, _ = _fake_mutmut({"whitney.mod.x_touched__mutmut_1": "killed"})

    _checks(run, mutants=mutants).mutation(RATCHETS, TOUCHED, stage="task")

    assert (mutants / "mutmut-stats.json").exists()


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


class _StageRecorder(_check["Checks"]):
    """Checks whose steps only note that they ran, so main's choice of steps can be tested."""

    def __init__(self, base, today):
        super().__init__(base, today, run=lambda command: None, mutants=Path("unused"))
        self.ran = []

    def __getattribute__(self, name):
        steps = ("floor", "secrets", "tests_and_coverage", "changed_line_coverage",
                 "coverage_ratchet", "mutation", "installed_package", "dependencies", "touched_prefixes")
        if name in steps:
            return lambda *args, **kwargs: object.__getattribute__(self, "ran").append(name)
        return object.__getattribute__(self, name)


@pytest.fixture
def recorded_stages(monkeypatch):
    made = []

    def make(base, today, max_children=None):
        made.append(_StageRecorder(base, today))
        made[-1].max_children = max_children
        return made[-1]

    monkeypatch.setitem(_check["main"].__globals__, "Checks", make)
    monkeypatch.setitem(_check["main"].__globals__, "read_ratchets", lambda text: RATCHETS)
    return made


def test_main_hands_the_worker_cap_to_the_checks(recorded_stages):
    _check["main"](["full", "--max-children", "6"])
    _check["main"](["full"])

    assert [c.max_children for c in recorded_stages] == [6, None]


@pytest.mark.parametrize("stage", ["task", "full"])
def test_skip_mutation_runs_every_other_check_but_no_mutants_and_warns(recorded_stages, capsys, stage):
    code = _check["main"]([stage, "--skip-mutation"])

    checks = recorded_stages[0]
    assert "mutation" not in checks.ran
    assert "tests_and_coverage" in checks.ran
    assert ("dependencies" in checks.ran) == (stage == "full")
    assert ("installed_package" in checks.ran) == (stage == "full")
    assert _result(checks, "mutation testing")[1] == "warn"
    assert "[warn] mutation testing: skipped (--skip-mutation)" in capsys.readouterr().out
    assert code == 0


@pytest.mark.parametrize("stage", ["task", "full"])
def test_without_skip_mutation_the_stage_runs_mutation(recorded_stages, stage):
    _check["main"]([stage])

    assert "mutation" in recorded_stages[0].ran
    assert _result(recorded_stages[0], "mutation testing") is None
