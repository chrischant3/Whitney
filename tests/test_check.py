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
