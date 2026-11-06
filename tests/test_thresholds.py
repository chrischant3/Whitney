"""The eval gate's minimums live in whitney.thresholds, and report.py still offers them."""

import subprocess
import sys

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

    assert getattr(report, name) == getattr(thresholds, name)
