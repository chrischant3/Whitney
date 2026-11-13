"""The vendored PR risk scorer (`.github/risk/score.py`) gives the scores CI relies on.

The scorer is thoughtbot's, copied unchanged (see `.github/risk/README.md`), so
these tests pin the behaviour the workflow depends on rather than its code:
a diff on stdin gives one JSON object, the same one every time.
"""

import json
import subprocess
import sys
from pathlib import Path

SCORER = Path(__file__).resolve().parents[1] / ".github" / "risk" / "score.py"


def diff_adding(path: str, lines: list[str]) -> str:
    """A unified diff that adds `path` with the given lines."""
    body = "".join(f"+{line}\n" for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n"
        f"@@ -0,0 +1,{len(lines)} @@\n{body}"
    )


def score(diff: str) -> dict:
    """Run the scorer on `diff` and return its JSON."""
    run = subprocess.run([sys.executable, "-I", str(SCORER)], input=diff, capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def rules(result: dict) -> set[str]:
    """The names of the rules that fired."""
    return {signal["rule"] for signal in result["signals"]}


def test_an_empty_diff_is_low_risk():
    result = score("")
    assert (result["score"], result["level"], result["files_changed"]) == (0, "low", 0)


def test_a_docs_only_change_is_low_risk():
    result = score(diff_adding("docs/notes.md", ["one line"]))
    assert (result["score"], result["level"], result["signals"]) == (0, "low", [])


def test_code_without_a_test_is_flagged_untested():
    result = score(diff_adding("src/orders.py", ["x = 1"]))
    assert rules(result) == {"untested_code"}
    assert result["score"] == 2


def test_code_with_a_test_is_not_flagged_untested():
    diff = diff_adding("src/orders.py", ["x = 1"]) + diff_adding("tests/test_orders.py", ["def test_x(): pass"])
    assert "untested_code" not in rules(score(diff))


def test_a_workflow_change_is_infra_config():
    result = score(diff_adding(".github/workflows/ci.yml", ["name: ci"]))
    assert rules(result) == {"infra_config"}


def test_a_destructive_migration_scores_medium():
    result = score(diff_adding("db/migrate/20261103_drop_status.rb", ["remove_column :orders, :status"]))
    assert rules(result) == {"migration", "destructive_migration"}
    assert (result["score"], result["level"]) == (5, "medium")


def test_a_destructive_migration_with_untested_code_scores_high():
    diff = diff_adding("db/migrate/20261103_drop_status.rb", ["remove_column :orders, :status"]) + diff_adding(
        "src/orders.py", ["x = 1"]
    )
    result = score(diff)
    assert (result["score"], result["level"]) == (7, "high")


def test_the_levels_change_at_three_and_six_points():
    dependency = diff_adding("package.json", ["{}"])
    untested_auth_code = diff_adding("src/auth/login.py", ["x = 1"])
    forty_nine_lines = diff_adding("docs/a.md", ["x"] * 49)
    scores = [score(diff) for diff in (dependency, dependency + forty_nine_lines, untested_auth_code, untested_auth_code + forty_nine_lines)]
    assert [(r["score"], r["level"]) for r in scores] == [(2, "low"), (3, "medium"), (5, "medium"), (6, "high")]


def test_a_large_diff_adds_points_for_size():
    result = score(diff_adding("docs/big.md", ["line"] * 500))
    assert [s["points"] for s in result["signals"] if s["rule"] == "large_diff"] == [3]
    assert result["lines_changed"] == 500


def test_lockfile_lines_do_not_count_toward_size():
    result = score(diff_adding("package-lock.json", ["line"] * 500))
    assert "large_diff" not in rules(result)


def test_the_same_diff_gives_the_same_score_every_time():
    diff = diff_adding("src/auth/login.py", ["x = 1"]) + diff_adding("pyproject.toml", ["[project]"])
    assert score(diff) == score(diff)
