"""The colocated-tests example in examples/colocated-tests: a rule that says what to do, tested with file_added."""

from pathlib import Path

import pytest

from whitney import cli
from whitney.evalfile import discover

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "colocated-tests"


@pytest.mark.parametrize("target", ["kilo", "claude-code"])
def test_the_colocated_tests_example_validates_with_no_problems_or_warnings(target, capsys):
    code = cli.main(["validate", "--target", target, "--project-path", str(EXAMPLE), "--evals-path", str(EXAMPLE / "evals")])

    assert capsys.readouterr().out.splitlines()[-1] == f"{EXAMPLE / 'evals'}: 1 eval file(s), 0 problem(s), 0 warning(s)"
    assert code == 0


def test_the_example_asks_for_a_new_test_beside_a_changed_module_against_a_baseline_without_the_rule():
    [eval_file] = discover(EXAMPLE / "evals")

    assert eval_file.source == "AGENTS.md"
    assert [variant.role for variant in eval_file.variants.values()] == ["baseline"]
    expects = [e.model_dump(exclude_none=True) for scenario in eval_file.resolved() for e in scenario.expect]
    assert expects and all(
        e == {"check": "file_added", "paths": ["*test_*.py"], "near": ["shop/*.py"]} for e in expects
    )
