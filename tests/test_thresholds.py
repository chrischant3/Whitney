"""The eval gate's minimums live in whitney.thresholds, and report.py still offers them."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest


def test_the_thresholds_import_nothing_else_from_whitney():
    # whitney.checks and whitney.report both read these, so this module must depend on neither.
    loaded = subprocess.run(
        [sys.executable, "-c", "import sys, whitney.thresholds; print(sorted(m for m in sys.modules if m.startswith('whitney')))"],
        capture_output=True, text=True, check=True,
    )
    assert loaded.stdout.strip() == "['whitney', 'whitney.thresholds']"


def test_the_thresholds_keep_todays_values():
    from whitney.thresholds import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD

    assert (MIN_ADHERENCE, MIN_HELD, MIN_CONCLUSIVE) == (0.8, 0.9, 0.5)


@pytest.mark.parametrize("name", ["MIN_ADHERENCE", "MIN_HELD", "MIN_CONCLUSIVE"])
def test_report_still_offers_each_threshold_under_its_old_name(name):
    from whitney import report, thresholds

    assert getattr(report, name) is getattr(thresholds, name)


THRESHOLD_NAMES = {"MIN_ADHERENCE", "MIN_HELD", "MIN_CONCLUSIVE"}
PACKAGE = Path(__file__).resolve().parents[1] / "src" / "whitney"


def _bindings_other_than_import_from_thresholds(tree: ast.AST) -> list[int]:
    """Line numbers where a threshold name is bound, except `from whitney.thresholds import NAME`."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in THRESHOLD_NAMES:
            lines.append(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in THRESHOLD_NAMES:
            lines.append(node.lineno)
        elif isinstance(node, ast.arg) and node.arg in THRESHOLD_NAMES:
            lines.append(node.lineno)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            from_thresholds = isinstance(node, ast.ImportFrom) and node.module == "whitney.thresholds"
            for alias in node.names:
                bound = alias.asname or alias.name
                if bound in THRESHOLD_NAMES and not (from_thresholds and alias.name == bound):
                    lines.append(node.lineno)
    return lines


def test_no_other_module_binds_a_threshold_name_except_by_importing_it():
    # The floor guard watches only thresholds.py's lines, so a second assignment elsewhere
    # (MIN_HELD = 0.5 in report.py, say) would shadow the guarded value without being flagged.
    found = {
        f"{path.relative_to(PACKAGE)}:{line}"
        for path in sorted(PACKAGE.rglob("*.py"))
        if path != PACKAGE / "thresholds.py"
        for line in _bindings_other_than_import_from_thresholds(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert found == set()


@pytest.mark.parametrize(
    "source",
    [
        "MIN_HELD = 0.5",
        "MIN_HELD: float = 0.5",
        "MIN_HELD += 0.1",
        "MIN_ADHERENCE, x = 0.5, 1",
        "for MIN_CONCLUSIVE in (0.1,): pass",
        "def MIN_HELD(): pass",
        "def f(MIN_HELD=0.5): pass",
        "from whitney.thresholds import MIN_ADHERENCE as MIN_HELD",
        "from elsewhere import MIN_HELD",
        "import elsewhere as MIN_HELD",
    ],
)
def test_the_shadowing_scan_sees_each_way_of_binding_a_name(source):
    assert _bindings_other_than_import_from_thresholds(ast.parse(source)) == [1]


def test_the_shadowing_scan_allows_importing_from_the_thresholds_module():
    source = "from whitney.thresholds import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD\nx = MIN_HELD"
    assert _bindings_other_than_import_from_thresholds(ast.parse(source)) == []
