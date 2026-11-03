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
