"""The consumer example in examples/consumer: a project with its own rules and evals, as a fork would write them."""

import tomllib
from pathlib import Path

import pytest

from whitney import cli
from whitney.evalfile import discover

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = ROOT / "examples" / "consumer"


def test_the_consumer_example_validates_for_kilo_with_no_problems_or_warnings(capsys):
    code = cli.main(["validate", "--target", "kilo", "--project-path", str(CONSUMER), "--evals-path", str(CONSUMER / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{CONSUMER / 'evals'}: 2 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_consumer_example_tests_each_of_its_rule_files_with_both_new_checks():
    files = {f.source: f for f in discover(CONSUMER / "evals")}

    assert set(files) == {".kilo/rules/data-handling.md", "AGENTS.md"}
    checks = {
        source: {e.check for scenario in f.resolved() for e in scenario.expect} for source, f in files.items()
    }
    assert checks == {".kilo/rules/data-handling.md": {"no_string_added", "no_secret_committed"}, "AGENTS.md": {"no_file_edits"}}


@pytest.mark.parametrize("folder", ["examples/"])
def test_mutmut_copies_the_folders_the_tests_read(folder):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["mutmut"]

    assert folder in config["also_copy"]
