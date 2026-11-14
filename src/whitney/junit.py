"""The gate's results as JUnit XML, which CI systems read: `whitney run --junit FILE` and `whitney report --junit FILE`.

The XML follows the Apache Ant JUnit task's schema, JUnit.xsd (a copy, with
its source and licence, is in tests/data/junit/, and the tests validate
against it). README, "JUnit XML for your CI system", lists the elements used:

- One `<testsuite>` per eval file the logs hold, named by the file's path
  under the evals root (`package` is its eval name), in the report's order.
  It has a `<testcase>` for each check its runs were scored with, then one
  named `gate`.
- A check's case counts that check's own outcome in the template's runs,
  leaving out known gaps, as the gate does. It fails if its pass rate is
  below the minimum the gate applies to the file (report.gate_minimum), with
  the explanation of each failed run, and is skipped if no run was
  conclusive, with each run's reason. Its rate and minimum are the suite's
  `<check>.rate` and `<check>.minimum` properties.
- The `gate` case fails with the gate's problem lines for that file.
- A last suite, `whitney`, has one case, `gate`, that fails with every
  problem the gate found, failed logs and missing files included: it fails
  whenever the gate fails. A run that couldn't start writes no file.

`time` is 0 and `hostname` is `localhost` throughout: Whitney doesn't time
checks, and the machine's name stays out of a file that may be published.
IFEvalCode and Harbor logs, and logs that aren't `success`, get no suite.
A run that errored is a reason in a check's `<skipped>`, so `errors` is always 0.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from whitney.checks import OUTCOMES
from whitney.report import (
    EVALS,
    LEGACY_CHECKS,
    TEMPLATE_VARIANT,
    Record,
    check_thresholds,
    gate_minimum,
    records_from_log,
    summarise,
)

if TYPE_CHECKING:
    from inspect_ai.log import EvalLog

GATE = "gate"
WHITNEY_SUITE = "whitney"
# XML 1.0 can't hold these at all, escaped or not; a harness's stderr tail in an explanation can carry them.
NOT_XML = re.compile("[^\t\n\r\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


@dataclass
class CheckRuns:
    """One check's outcomes in the template's runs of one eval file: each run's name and explanation, by outcome."""

    runs: dict[str, list[str]] = field(default_factory=lambda: {outcome: [] for outcome in OUTCOMES})

    @property
    def rate(self) -> float | None:
        """Passes over conclusive runs, or None if none was conclusive."""
        conclusive = len(self.runs["pass"]) + len(self.runs["fail"])
        return len(self.runs["pass"]) / conclusive if conclusive else None


def junit_xml(
    logs: list[EvalLog], problems: list[str], *, min_rates: Mapping[str, float], min_conclusive: float, timestamp: datetime,
) -> str:
    """The JUnit XML document for a run's logs and its gate, as a string (see the module's docstring).

    Args:
        logs: the run's eval-file logs that finished as `success` (write_report's `succeeded`):
            not IFEvalCode or Harbor logs, nor logs that failed, whose problems are in `problems`.
        problems: every problem the gate found, as write_report lists them.
        min_rates: the check minimums replaced for this run (report.replaced_minimums).
        min_conclusive: the share of template runs that must be conclusive.
        timestamp: when the report was made; each suite's `timestamp`, to the second.
    """
    paired = [pair for log in logs for pair in zip(records_from_log(log), log.samples or [])]
    summary = summarise([record for record, _ in paired])
    root = ET.Element("testsuites")
    when = f"{timestamp:%Y-%m-%dT%H:%M:%S}"
    for key, eval_summary in summary.items():
        eval_name = eval_summary.file.eval_name if eval_summary.file else key
        minimum = gate_minimum(key, eval_summary, min_rates)
        checks = _check_runs(key, [(record, sample) for record, sample in paired if _key(record) == key])
        suite = _suite(root, key, eval_name, when)
        output = []
        for name, runs in checks.items():
            _property(suite, f"{name}.rate", "none" if runs.rate is None else f"{runs.rate:g}")
            _property(suite, f"{name}.minimum", f"{minimum:g}")
            output += _check_case(suite, eval_name, name, runs, minimum)
        own = check_thresholds({key: eval_summary}, min_conclusive=min_conclusive, min_rates=min_rates)
        gate = [problem.removeprefix(f"{eval_name}: ") for problem in own]
        _case(suite, eval_name, GATE, ("failure", "; ".join(gate), "\n".join(gate)) if gate else None)
        output += [f"{GATE}: {problem}" for problem in gate]
        _finish(suite, output)
    suite = _suite(root, WHITNEY_SUITE, WHITNEY_SUITE, when)
    _case(suite, WHITNEY_SUITE, GATE, ("failure", "; ".join(problems), "\n".join(problems)) if problems else None)
    _finish(suite, [f"{GATE}: {problem}" for problem in problems])
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"


def _key(record: Record) -> str:
    """The record's eval file's key in the summary, as report.summarise keys it."""
    return record.file.eval_file if record.file else record.eval_name


def _check_runs(key: str, paired: list[tuple[Record, object]]) -> dict[str, CheckRuns]:
    """Each check's outcomes over the template's runs that aren't known gaps.

    The checks are the file's own, in its order, then any other a score
    names. Where a score doesn't record a check's outcome in the shape
    tasks._scenario_score writes it, the run's own outcome stands for each
    of the file's checks, explained by the score's explanation.
    """
    first = paired[0][0]  # summarise keys a file only by its records, so it has at least one
    names = list(first.file.checks) if first.file else [LEGACY_CHECKS[key]]
    checks = {name: CheckRuns() for name in names}
    for record, sample in paired:
        if record.variant != TEMPLATE_VARIANT or record.metadata.get("known_gap"):
            continue
        run = f"{record.scenario}, epoch {sample.epoch}"
        for name, outcome, explanation in _outcomes(record, sample, names):
            checks.setdefault(name, CheckRuns()).runs[outcome].append(f"{run}: {explanation}")
    return checks


def _outcomes(record: Record, sample: object, names: list[str]) -> list[tuple[str, str, str]]:
    """(check, outcome, explanation) for each check the run was scored with."""
    if sample.error or not sample.scores:
        reason = f"the run errored: {sample.error.message}" if sample.error else "the run has no score"
        return [(name, "inconclusive", reason) for name in names]
    score = next(iter(sample.scores.values()))
    recorded = (getattr(score, "metadata", None) or {}).get("checks")
    if isinstance(recorded, list) and recorded and all(_is_check_entry(entry) for entry in recorded):
        return [(entry["check"], entry["outcome"], entry["explanation"]) for entry in recorded]
    passing, failing, _ = record.file.labels if record.file else EVALS[record.eval_name][:3]
    outcome = "pass" if record.outcome == passing else "fail" if record.outcome == failing else "inconclusive"
    explanation = score.explanation if isinstance(getattr(score, "explanation", None), str) else ""
    return [(name, outcome, explanation) for name in names]


def _is_check_entry(entry: object) -> bool:
    """A check's entry in a score's metadata, as tasks._check_record writes it."""
    return (
        isinstance(entry, dict) and isinstance(entry.get("check"), str) and entry.get("outcome") in OUTCOMES
        and isinstance(entry.get("explanation"), str)
    )


def _check_case(suite: ET.Element, eval_name: str, name: str, runs: CheckRuns, minimum: float) -> list[str]:
    """Add one check's case to the suite; return its lines for the suite's system-out."""
    passed, failed, inconclusive = (runs.runs[outcome] for outcome in OUTCOMES)
    counted = f"{len(passed)} of {len(passed) + len(failed)} conclusive template runs passed"
    if runs.rate is None:
        result = ("skipped", f"no conclusive template runs: {len(inconclusive)} inconclusive", "\n".join(inconclusive))
        line = f"{name}: no conclusive template runs; {len(inconclusive)} inconclusive"
    else:
        shown = f"{counted} ({runs.rate:.0%})"
        below = runs.rate < minimum
        result = ("failure", f"{shown}, below the minimum {minimum:.0%}", "\n".join(failed)) if below else None
        line = f"{name}: {shown}, minimum {minimum:.0%}" + (f"; {len(inconclusive)} inconclusive" if inconclusive else "")
    _case(suite, eval_name, name, result)
    return [line, *(f"- fail: {run}" for run in failed), *(f"- inconclusive: {run}" for run in inconclusive)]


def _suite(root: ET.Element, name: str, package: str, when: str) -> ET.Element:
    """A new, empty testsuite under `root`, numbered from 0 as the schema asks; _finish fills in its counts."""
    suite = ET.SubElement(root, "testsuite", {
        "id": str(len(root)), "package": _clean(package), "name": _clean(name), "timestamp": when, "hostname": "localhost",
        "time": "0",
    })
    ET.SubElement(suite, "properties")
    return suite


def _property(suite: ET.Element, name: str, value: str) -> None:
    """Add a property, such as a check's rate or minimum, to the suite's `<properties>`."""
    ET.SubElement(suite.find("properties"), "property", {"name": _clean(name), "value": _clean(value)})


def _case(suite: ET.Element, classname: str, name: str, result: tuple[str, str, str] | None) -> None:
    """Add a testcase; `result` is (element, message, text) for a failure or a skip, or None for a pass."""
    case = ET.SubElement(suite, "testcase", {"classname": _clean(classname), "name": _clean(name), "time": "0"})
    if result:
        kind, message, text = result
        attributes = {"message": _clean(message)}
        if kind == "failure":
            attributes["type"] = "below minimum" if name != GATE else GATE
        ET.SubElement(case, kind, attributes).text = _clean(text)


def _finish(suite: ET.Element, output: list[str]) -> None:
    """Count the suite's cases and add the system-out and system-err the schema requires."""
    found = suite.findall("testcase")
    suite.set("tests", str(len(found)))
    suite.set("failures", str(sum(case.find("failure") is not None for case in found)))
    suite.set("errors", "0")
    suite.set("skipped", str(sum(case.find("skipped") is not None for case in found)))
    ET.SubElement(suite, "system-out").text = _clean("\n".join(output))
    ET.SubElement(suite, "system-err")


def _clean(text: str) -> str:
    """`text` with each character XML can't hold replaced by U+FFFD."""
    return NOT_XML.sub("\ufffd", text)
