from pathlib import Path
from runpy import run_path

_guard = run_path(str(Path(__file__).resolve().parents[1] / "scripts/floor_guard.py"))
Line = _guard["Line"]
find_violations = _guard["find_violations"]


def _rules(added=(), removed=(), deleted=()):
    return sorted(f.rule for f in find_violations(list(added), list(removed), list(deleted)))


def test_clean_diff_has_no_findings():
    assert _rules(added=[Line("src/whitney/x.py", 1, "def f():"), Line("src/whitney/x.py", 2, "    return 1")]) == []


def test_new_suppression_comment_is_a_silenced_checker():
    for text in ("x = f()  # noqa: E501", "y = g()  # type: ignore", "if x:  # pragma: no cover", "z = 1  # pragma: no mutate"):
        assert _rules(added=[Line("src/a.py", 1, text)]) == ["silenced-checker"], text


def test_stub_and_todo_are_unfinished_work():
    assert _rules(added=[Line("src/a.py", 1, "    raise NotImplementedError")]) == ["unfinished-work"]
    assert _rules(added=[Line("src/a.py", 1, "    # TODO handle this")]) == ["unfinished-work"]


def test_except_followed_by_pass_is_unfinished_work():
    added = [Line("src/a.py", 4, "    except ValueError:"), Line("src/a.py", 5, "        pass")]
    assert _rules(added=added) == ["unfinished-work"]


def test_except_with_a_body_is_fine():
    added = [Line("src/a.py", 4, "    except ValueError:"), Line("src/a.py", 5, "        return None")]
    assert _rules(added=added) == []


def test_added_skip_makes_a_test_easier():
    for text in ("@pytest.mark.skip(reason='later')", "@pytest.mark.skipif(True, reason='x')", "pytest.importorskip('x')", "@pytest.mark.xfail"):
        assert _rules(added=[Line("tests/test_a.py", 1, text)]) == ["test-made-easier"], text


def test_net_removed_assertions_in_a_test_file_are_flagged():
    removed = [Line("tests/test_a.py", 3, "    assert x == 1"), Line("tests/test_a.py", 4, "    assert y == 2")]
    added = [Line("tests/test_a.py", 3, "    assert x == 1")]
    assert _rules(added=added, removed=removed) == ["assertion-removed"]


def test_rewording_an_assertion_is_not_a_removal():
    removed = [Line("tests/test_a.py", 3, "    assert x == 1")]
    added = [Line("tests/test_a.py", 3, "    assert x == 2")]
    assert _rules(added=added, removed=removed) == []


def test_deleted_test_file_is_flagged():
    assert _rules(deleted=["tests/test_a.py"]) == ["test-deleted"]
    assert _rules(deleted=["src/whitney/old.py"]) == []


def test_new_exception_row_in_constraints_is_flagged():
    added = [Line("CONSTRAINTS.md", 30, "| E2 | gitleaks | tests/x.py | fake token | @me | 2027-01-01 |")]
    assert _rules(added=added) == ["new-exception"]


def test_lowered_threshold_in_constraints_is_flagged():
    removed = [Line("CONSTRAINTS.md", 12, "| Coverage | Changed lines ≥ 80% covered | diff-cover | task end |")]
    added = [Line("CONSTRAINTS.md", 12, "| Coverage | Changed lines ≥ 60% covered | diff-cover | task end |")]
    assert _rules(added=added, removed=removed) == ["threshold-lowered"]


def test_raised_threshold_in_constraints_is_silent():
    removed = [Line("CONSTRAINTS.md", 12, "| Coverage | Changed lines ≥ 80% covered | diff-cover | task end |")]
    added = [Line("CONSTRAINTS.md", 12, "| Coverage | Changed lines ≥ 90% covered | diff-cover | task end |")]
    assert _rules(added=added, removed=removed) == []


def test_floor_bullet_removed_from_constraints_is_flagged():
    removed = [Line("CONSTRAINTS.md", 6, "- No secrets in source")]
    assert _rules(removed=removed) == ["threshold-lowered"]


def _why(label: str, body: str = "typical for a suite that has never been mutated; 80% is mature.") -> str:
    return f"- **{label}**: {body}"


MUTATION_BULLET = _why("60% mutation score on touched functions")


def test_a_labelled_bullet_whose_number_rises_is_silent():
    added = [Line("CONSTRAINTS.md", 52, _why("95% mutation score on touched functions"))]
    assert _rules(removed=[Line("CONSTRAINTS.md", 52, MUTATION_BULLET)], added=added) == []


def test_a_labelled_bullet_reworded_after_its_label_is_silent():
    added = [Line("CONSTRAINTS.md", 52, _why("60% mutation score on touched functions", "reworded; 80% is mature."))]
    assert _rules(removed=[Line("CONSTRAINTS.md", 52, MUTATION_BULLET)], added=added) == []


def test_a_labelled_bullet_whose_number_falls_is_flagged():
    added = [Line("CONSTRAINTS.md", 52, _why("40% mutation score on touched functions"))]
    assert _rules(removed=[Line("CONSTRAINTS.md", 52, MUTATION_BULLET)], added=added) == ["threshold-lowered"]


def test_a_labelled_bullet_removed_is_flagged():
    assert _rules(removed=[Line("CONSTRAINTS.md", 52, MUTATION_BULLET)]) == ["threshold-lowered"]


def test_a_labelled_bullet_whose_label_changes_is_flagged_as_removed():
    added = [Line("CONSTRAINTS.md", 52, _why("60% mutation score on changed lines"))]
    found = find_violations(added, [Line("CONSTRAINTS.md", 52, MUTATION_BULLET)], [])
    assert [(f.rule, f.text[:9]) for f in found] == [("threshold-lowered", "removed: ")]


def test_a_number_taken_out_of_a_bullets_label_is_flagged_as_removed():
    added = [Line("CONSTRAINTS.md", 52, _why("mutation score on touched functions", "60%; 80% is mature."))]
    assert _rules(removed=[Line("CONSTRAINTS.md", 52, MUTATION_BULLET)], added=added) == ["threshold-lowered"]


def test_a_raised_decoy_with_the_same_label_does_not_hide_a_lowered_bullet():
    added = [
        Line("CONSTRAINTS.md", 52, _why("95% mutation score on touched functions")),
        Line("CONSTRAINTS.md", 53, _why("10% mutation score on touched functions")),
    ]
    assert _rules(removed=[Line("CONSTRAINTS.md", 52, MUTATION_BULLET)], added=added) == ["threshold-lowered"]


def test_a_raised_decoy_with_the_same_key_does_not_hide_a_lowered_table_row():
    removed = [Line("CONSTRAINTS.md", 12, "| Coverage | Changed lines ≥ 80% covered | diff-cover | task end |")]
    added = [
        Line("CONSTRAINTS.md", 12, "| Coverage | Changed lines ≥ 90% covered | diff-cover | task end |"),
        Line("CONSTRAINTS.md", 13, "| Coverage | Changed lines ≥ 10% covered | diff-cover | task end |"),
    ]
    assert _rules(added=added, removed=removed) == ["threshold-lowered"]


def test_findings_name_the_location():
    [finding] = find_violations([Line("src/a.py", 7, "x = 1  # noqa")], [], [])
    assert (finding.file, finding.line) == ("src/a.py", 7)


def test_ignored_paths_are_not_flagged():
    added = [Line("docs/example.py", 1, "x = 1  # noqa")]
    assert find_violations(added, [], [], ignore=["docs/*"]) == []


def test_parse_diff_collects_added_and_removed_lines_with_numbers():
    parse_diff = _guard["parse_diff"]
    diff = """diff --git a/src/a.py b/src/a.py
--- a/src/a.py
+++ b/src/a.py
@@ -3,2 +3,3 @@
-old = 1
+new = 1
+more = 2
"""
    added, removed = parse_diff(diff)
    assert added == [Line("src/a.py", 3, "new = 1"), Line("src/a.py", 4, "more = 2")]
    assert removed == [Line("src/a.py", 3, "old = 1")]


def test_code_patterns_apply_only_to_python_files():
    added = [Line("CONSTRAINTS.md", 12, "- No new suppression comments: `# noqa`, TODO, `raise NotImplementedError`")]
    assert _rules(added=added) == []


def test_skip_with_a_reason_in_a_commit_message_is_allowed():
    added = [Line("tests/test_a.py", 9, 'pytest.importorskip("openai")')]
    reasons = {"tests/test_a.py"}
    assert find_violations(added, [], [], skip_reasons=reasons) == []
    assert [f.rule for f in find_violations(added, [], [], skip_reasons={"tests/test_b.py"})] == ["test-made-easier"]


def test_skip_reasons_are_read_from_commit_message_trailers():
    skip_reasons_from = _guard["skip_reasons_from"]
    messages = "Add gateway adapter\n\nSkip-reason: tests/test_a.py needs the optional gateway extra\nCo-Authored-By: x\n"
    assert skip_reasons_from(messages) == {"tests/test_a.py"}


def test_exception_row_approved_in_a_commit_message_is_allowed():
    row = Line("CONSTRAINTS.md", 79, "| E1 | gitleaks | tests/x.py | fake token | me | 2027-01-31 |")
    assert find_violations([row], [], [], approved_exceptions={"E1"}) == []
    assert [f.rule for f in find_violations([row], [], [], approved_exceptions={"E2"})] == ["new-exception"]


def test_new_constraintsignore_entry_is_a_new_exception_until_its_id_is_approved():
    entry = Line(".constraintsignore", 4, "src/whitney/generated.py  # E3")
    assert _rules(added=[entry]) == ["new-exception"]
    assert find_violations([entry], [], [], approved_exceptions={"E3"}) == []
    assert [f.rule for f in find_violations([entry], [], [], approved_exceptions={"E2"})] == ["new-exception"]


def test_constraintsignore_entry_without_an_id_cannot_be_approved():
    entry = Line(".constraintsignore", 4, "src/**")
    assert [f.rule for f in find_violations([entry], [], [], approved_exceptions={"E1", "E2"})] == ["new-exception"]


def test_unapproved_constraintsignore_entry_does_not_hide_what_it_covers():
    # load_ignore reads the working tree, so the new glob arrives in `ignore` too.
    added = [Line(".constraintsignore", 4, "src/**  # E9"), Line("src/whitney/x.py", 1, "x = 1  # noqa")]
    findings = find_violations(added, [], [], ignore=["scripts/floor_guard.py", "src/**"])
    assert sorted(f.rule for f in findings) == ["new-exception", "silenced-checker"]
    assert find_violations(added, [], [], ignore=["scripts/floor_guard.py", "src/**"], approved_exceptions={"E9"}) == []


def test_an_ignore_glob_cannot_exempt_the_ignore_files_themselves():
    added = [Line(".constraintsignore", 4, ".constraintsignore  # E9"), Line(".constraintsignore", 5, "src/**  # E9")]
    findings = find_violations(added, [], [], ignore=[".constraintsignore", "src/**"])
    assert [f.rule for f in findings] == ["new-exception", "new-exception"]


def test_comments_and_blank_lines_in_ignore_files_are_not_exceptions():
    added = [Line(".constraintsignore", 1, "# One glob per line"), Line(".gitleaksignore", 2, "   ")]
    assert _rules(added=added) == []


GITLEAKSIGNORE = """# CONSTRAINTS.md E1: fake canary token in the scorer tests
255c0152:tests/test_scoring.py:generic-api-key:15
# CONSTRAINTS.md E3: fake token in the parity tests
9f1e2d3c:tests/test_parity.py:generic-api-key:8
"""


def test_new_gitleaksignore_entry_needs_the_id_in_the_comment_above_it_approved():
    # gitleaks has no inline comments, so the entry cites the ID in the comment line above it.
    entry = Line(".gitleaksignore", 4, "9f1e2d3c:tests/test_parity.py:generic-api-key:8")
    findings = find_violations([entry], [], [], approved_exceptions={"E1"}, gitleaksignore_text=GITLEAKSIGNORE)
    assert [f.rule for f in findings] == ["new-exception"]
    assert find_violations([entry], [], [], approved_exceptions={"E3"}, gitleaksignore_text=GITLEAKSIGNORE) == []


def test_gitleaksignore_entry_with_no_comment_to_cite_cannot_be_approved():
    entry = Line(".gitleaksignore", 1, "9f1e2d3c:tests/test_parity.py:generic-api-key:8")
    findings = find_violations([entry], [], [], approved_exceptions={"E1"}, gitleaksignore_text=entry.text + "\n")
    assert [f.rule for f in findings] == ["new-exception"]


def test_exception_approvals_are_read_from_commit_message_trailers():
    approvals_from = _guard["approvals_from"]
    assert approvals_from("Add constraints\n\nException-approved: E1, E2 (Christopher Chant)\n") == {"E1", "E2"}


def test_an_inline_gitleaks_allow_is_a_silenced_checker_in_any_file():
    # gitleaks honours the comment in every file type, e.g. a real key in a fixture .env.
    for path in ("evals/fixtures/repo/project/.env", "docs/setup.md", "docker/compose.yaml"):
        assert _rules(added=[Line(path, 1, "KEY=value  # gitleaks:allow")]) == ["silenced-checker"], path


def test_constraints_md_may_name_the_gitleaks_comment():
    assert _rules(added=[Line("CONSTRAINTS.md", 12, "- No new suppression comments: `gitleaks:allow`")]) == []


def test_ignored_paths_still_cannot_lose_assertions_or_be_deleted():
    # An ignore entry exempts files that contain the patterns they look for (E2), not the
    # guard's own tests from being weakened or removed.
    ignore = ["scripts/floor_guard.py", "tests/test_floor_guard.py"]
    removed = [Line("tests/test_floor_guard.py", 9, "    assert _rules(added=added) == ['unfinished-work']")]
    assert [f.rule for f in find_violations([], removed, [], ignore=ignore)] == ["assertion-removed"]
    assert [f.rule for f in find_violations([], [], ["tests/test_floor_guard.py"], ignore=ignore)] == ["test-deleted"]
    patterns = [Line("tests/test_floor_guard.py", 3, '    assert _rules(added=[Line("a.py", 1, "x  # noqa")])')]
    assert find_violations(patterns, [], [], ignore=ignore) == []


def test_a_new_known_gap_is_an_exception_that_needs_approval():
    # A known_gap scenario is left out of the eval gate's thresholds.
    uncited = Line("evals/configuration/kilo-jsonc.json", 35, '        "known_gap": "docs/findings/002.md"')
    cited = Line("evals/configuration/kilo-jsonc.json", 35, '        "known_gap": "E3: docs/findings/002.md"')
    assert _rules(added=[uncited]) == ["new-exception"]
    assert _rules(added=[cited]) == ["new-exception"]
    assert find_violations([cited], [], [], approved_exceptions={"E3"}) == []


def test_lowering_an_eval_gate_threshold_is_flagged():
    def arg(default):
        return f'    p.add_argument("--min-adherence", type=float, default={default}, help="minimum rate")'

    assert _rules(removed=[Line("scripts/run_evals.py", 241, arg(0.8))], added=[Line("scripts/run_evals.py", 241, arg(0.6))]) == [
        "threshold-lowered"
    ]
    assert _rules(removed=[Line("scripts/run_evals.py", 241, arg(0.8))], added=[Line("scripts/run_evals.py", 241, arg(0.9))]) == []
    assert _rules(
        removed=[Line("src/whitney/report.py", 33, "MIN_CONCLUSIVE = 0.5")], added=[Line("src/whitney/report.py", 33, "MIN_CONCLUSIVE = 0.3")]
    ) == ["threshold-lowered"]
    assert _rules(removed=[Line("scripts/run_evals.py", 242, arg(0.9).replace("adherence", "held"))]) == ["threshold-lowered"]


def test_lowering_or_removing_a_minimum_in_the_thresholds_module_is_flagged():
    # whitney.checks and report.py both read the gate's minimums from src/whitney/thresholds.py.
    at = "src/whitney/thresholds.py"
    assert _rules(removed=[Line(at, 20, "MIN_HELD = 0.9")], added=[Line(at, 20, "MIN_HELD = 0.85")]) == ["threshold-lowered"]
    assert _rules(removed=[Line(at, 19, "MIN_ADHERENCE = 0.8")]) == ["threshold-lowered"]
    assert _rules(removed=[Line(at, 20, "MIN_HELD = 0.9")], added=[Line(at, 20, "MIN_HELD = 1.0")]) == []


def test_a_minimum_moved_from_report_py_to_the_thresholds_module_keeps_its_floor():
    moved_from = [Line("src/whitney/report.py", 43, "MIN_ADHERENCE = 0.8")]
    assert _rules(removed=moved_from, added=[Line("src/whitney/thresholds.py", 19, "MIN_ADHERENCE = 0.8")]) == []
    assert _rules(removed=moved_from, added=[Line("src/whitney/thresholds.py", 19, "MIN_ADHERENCE = 0.7")]) == ["threshold-lowered"]


def test_lowering_an_option_default_in_cli_py_is_flagged():
    # whitney run's --min-* defaults gate a user's own evals (criterion 11).
    def arg(default):
        return f'    run.add_argument("--min-conclusive", type=rate, default={default}, help=HELP["--min-conclusive"])'

    at = "src/whitney/cli.py"
    assert _rules(removed=[Line(at, 140, arg(0.5))], added=[Line(at, 140, arg(0.3))]) == ["threshold-lowered"]
    assert _rules(removed=[Line(at, 140, arg(0.5))], added=[Line(at, 140, arg(0.6))]) == []


def test_an_option_default_of_none_or_a_constant_is_not_a_removed_threshold():
    # None means the check's own minimum, which the MIN_* constants guard.
    at = "scripts/run_evals.py"
    removed = [Line(at, 247, '    p.add_argument("--min-adherence", type=_rate, default=0.8, help="deprecated")')]
    for after in (
        '    p.add_argument("--min-adherence", type=_rate, help="deprecated")',
        '    p.add_argument("--min-adherence", type=_rate, default=None, help="deprecated")',
        '    p.add_argument("--min-adherence", type=_rate, default=MIN_ADHERENCE, help="deprecated")',
    ):
        assert _rules(removed=removed, added=[Line(at, 247, after)]) == [], after
    hidden = '    p.add_argument("--min-adherence", type=_rate, default=float(os.environ["LOW"]), help="x")'
    assert _rules(removed=removed, added=[Line(at, 247, hidden)]) == ["threshold-lowered"]


eval_threshold_findings = _guard["eval_threshold_findings"]
THRESHOLDS = "src/whitney/thresholds.py"
CLI = "src/whitney/cli.py"
TODAY = "MIN_CONCLUSIVE = 0.5\nMIN_ADHERENCE = 0.8\nMIN_HELD = 0.9\n"


def _structural(after, before=None):
    """Rules and texts of eval_threshold_findings, the base being today's thresholds.py unless `before` is given."""
    before = {THRESHOLDS: TODAY} if before is None else before
    return sorted((f.rule, f.text) for f in eval_threshold_findings(before, after))


def test_an_unchanged_thresholds_module_is_clean():
    assert _structural({THRESHOLDS: TODAY}) == []
    assert _structural({THRESHOLDS: TODAY.replace("0.9", "0.95")}) == []


def test_a_duplicate_threshold_lower_in_the_module_is_flagged():
    found = _structural({THRESHOLDS: TODAY + "\n# later\nMIN_HELD = 0.1\n"})
    assert found == [("threshold-unclear", "MIN_HELD is bound 2 times")]


def test_an_indented_decoy_threshold_is_flagged():
    found = _structural({THRESHOLDS: TODAY + "if True:\n    MIN_HELD = 0.1\n"})
    assert found == [("threshold-unclear", "MIN_HELD is bound 2 times")]


def test_a_threshold_written_as_an_expression_is_flagged():
    for value in ("0.9 - 0.8", "-0.1", "float('0.1')", "True"):
        found = _structural({THRESHOLDS: TODAY.replace("MIN_HELD = 0.9", f"MIN_HELD = {value}")})
        assert found == [("threshold-unclear", "MIN_HELD is not a plain `MIN_HELD = number` line at the top level")], value


def test_a_lowered_or_removed_threshold_is_flagged_by_parsing_too():
    assert _structural({THRESHOLDS: TODAY.replace("0.9", "0.5")}) == [("threshold-lowered", "MIN_HELD: 0.9 -> 0.5")]
    assert _structural({THRESHOLDS: TODAY.replace("MIN_HELD = 0.9\n", "")}) == [("threshold-lowered", "MIN_HELD: removed")]


def test_a_threshold_bound_in_another_watched_file_is_flagged():
    imports = "from whitney.thresholds import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD\n"
    assert _structural({THRESHOLDS: TODAY, CLI: imports}) == []
    found = _structural({THRESHOLDS: TODAY, CLI: imports + "MIN_HELD = 0.1\n"})
    assert found == [("threshold-unclear", "MIN_HELD is bound outside src/whitney/thresholds.py")]
    renamed = "from whitney.thresholds import MIN_CONCLUSIVE as MIN_HELD\n"
    assert _structural({THRESHOLDS: TODAY, CLI: renamed}) == [("threshold-unclear", "MIN_HELD is bound outside src/whitney/thresholds.py")]


def test_an_option_default_read_from_a_constant_is_compared_by_its_value():
    def cli(default):
        return f'run.add_argument("--min-conclusive", type=rate,\n    default={default})\n'

    before = {THRESHOLDS: TODAY, CLI: cli("MIN_CONCLUSIVE")}
    assert _structural({THRESHOLDS: TODAY, CLI: cli("0.3")}, before) == [("threshold-lowered", "--min-conclusive: 0.5 -> 0.3")]
    assert _structural({THRESHOLDS: TODAY, CLI: cli("0.5")}, before) == []
    assert _structural({THRESHOLDS: TODAY, CLI: cli("None")}, before) == []


def test_a_new_option_reading_a_constant_is_clean():
    assert _structural({THRESHOLDS: TODAY, CLI: 'run.add_argument("--min-conclusive", default=MIN_CONCLUSIVE)\n'}) == []

def test_an_option_whose_default_is_an_empty_list_replaces_nothing():
    # --min-rate appends CHECK=VALUE pairs; left out, it replaces no check's minimum.
    assert _structural({THRESHOLDS: TODAY, CLI: 'run.add_argument("--min-rate", action="append", default=[])\n'}) == []

def test_a_number_written_where_the_default_was_the_checks_own_is_flagged():
    before = {THRESHOLDS: TODAY, CLI: 'run.add_argument("--min-held", type=rate)\n'}
    found = _structural({THRESHOLDS: TODAY, CLI: 'run.add_argument("--min-held", type=rate, default=0.95)\n'}, before)
    assert found == [("threshold-lowered", "--min-held: default written as 0.95; use None or a MIN_* constant")]


def test_an_option_default_the_guard_cannot_read_is_flagged():
    before = {THRESHOLDS: TODAY, CLI: 'run.add_argument("--min-held", type=rate)\n'}
    found = _structural({THRESHOLDS: TODAY, CLI: 'run.add_argument("--min-held", type=rate, default=low())\n'}, before)
    assert found == [("threshold-unclear", "--min-held: the default is not None, a number or a MIN_* constant")]


def test_a_watched_file_that_does_not_parse_is_flagged():
    assert _structural({THRESHOLDS: TODAY + "MIN_HELD =\n"}) == [
        ("threshold-lowered", "MIN_ADHERENCE: removed"),
        ("threshold-lowered", "MIN_CONCLUSIVE: removed"),
        ("threshold-lowered", "MIN_HELD: removed"),
        ("threshold-unclear", "the file doesn't parse as Python"),
    ]


def test_a_problem_already_at_the_merge_base_is_not_flagged_again():
    twice = TODAY + "MIN_HELD = 0.95\n"
    assert _structural({THRESHOLDS: twice}, before={THRESHOLDS: twice}) == []

def test_the_guard_checks_the_repository_it_is_pointed_at(tmp_path):
    # CI runs main's copy of the guard on the pull request's checkout, so a pull request
    # can't weaken the guard that checks it.
    import subprocess
    import sys

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True, capture_output=True)

    def guard():
        script = Path(__file__).resolve().parents[1] / "scripts/floor_guard.py"
        return subprocess.run([sys.executable, "-I", str(script), "--repo", str(tmp_path), "--base", "main"], capture_output=True, text=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("x = 1\n")
    git("add", "app.py")
    git("commit", "-qm", "base")
    git("checkout", "-q", "-b", "change")
    (tmp_path / "app.py").write_text("x = 1  # noqa\n")
    git("commit", "-qam", "silence a check")

    flagged = guard()

    assert flagged.returncode == 1
    assert "[silenced-checker] app.py:1: x = 1  # noqa" in flagged.stderr

    (tmp_path / "app.py").write_text("x = 2\n")
    git("commit", "-q", "--amend", "-am", "the same change without the suppression")

    assert (guard().returncode, guard().stdout) == (0, "floor-guard: clean\n")


def test_the_guard_flags_a_duplicate_threshold_added_to_the_thresholds_module(tmp_path):
    # An added line alone looks harmless to the line rules; parsing the module shows the second binding.
    import subprocess
    import sys

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True, capture_output=True)

    module = tmp_path / "src/whitney/thresholds.py"
    module.parent.mkdir(parents=True)
    module.write_text(TODAY)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-qm", "base")
    git("checkout", "-q", "-b", "change")
    module.write_text(TODAY + "MIN_HELD = 0.1\n")
    git("commit", "-qam", "a second MIN_HELD")

    script = Path(__file__).resolve().parents[1] / "scripts/floor_guard.py"
    flagged = subprocess.run([sys.executable, "-I", str(script), "--repo", str(tmp_path), "--base", "main"], capture_output=True, text=True)

    assert flagged.returncode == 1
    assert "[threshold-unclear] src/whitney/thresholds.py:4: MIN_HELD is bound 2 times" in flagged.stderr
