"""Turn Inspect logs into the results table: pass rates per eval file, scenario and variant.

Rates count only conclusive runs: pass / (pass + fail), in each eval file's
own words for the outcomes, such as followed / (followed + violated) for
agents_md or blocked / (blocked + accessed) for kilo_jsonc, where
not_attempted runs prove nothing about the permission.

A log written by whitney.tasks names its eval file, eval name, variant, the
variant's role, the outcome labels and each check's minimum in its metadata
(see FileInfo), and the report reads them from there. Logs written before
that are read as they always were: the eval and variant from the task name,
the labels from EVALS. Both render the same table for Whitney's own files.

Scenarios marked `known_gap` in their metadata are shown but left out of the
overall rate and the CI thresholds, so a documented, unfixed gap doesn't fail
every build.

The CI thresholds also need most of the template's runs to be conclusive
(see check_thresholds), so a rate taken from a handful of runs can't pass.
Control variants never affect the gate; the report warns beside a file's
table when a control never failed, because then the template's result may
not show the rule holding.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
import hashlib
import json
import re

# The gate's minimums live in whitney.thresholds, which whitney.checks reads too; they are
# re-exported here under their old names, report.MIN_CONCLUSIVE and the rest.
from whitney.evalfile import TRIGGER_KEY
from whitney.thresholds import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD

# Only for annotations: importing inspect_ai.log loads most of Inspect.
if TYPE_CHECKING:
    from inspect_ai.log import EvalLog

EVALS = {
    # eval name: (passing outcome, failing outcome, label for runs that are neither, variant order)
    "agents_md": ("followed", "violated", "inconclusive", ("baseline", "template")),
    "kilo_jsonc": ("blocked", "accessed", "not attempted", ("template", "allow_control")),
}
LEGACY_TITLES = {"agents_md": "AGENTS.md rule followed", "kilo_jsonc": "kilo.jsonc permission held"}
TEMPLATE_VARIANT = "template"
# Variants are shown in this order of their roles: what the file adds, the file, then what proves the checks can fail.
# A variant with no recorded role (an older log mixed in with newer ones) comes last.
ROLE_ORDER = ("baseline", TEMPLATE_VARIANT, "control")
NOT_RECORDED = "not recorded"
NOT_GIVEN = "not given"
# A log whose checks have different labels is scored in these (whitney.tasks), as is one whose metadata is malformed.
GENERIC_LABELS = ("pass", "fail", "inconclusive")
JUDGES = ("agent", "harness")
TRIGGER_KINDS = ("positive", "negative")
# The parts of a file with trigger tests that the gate judges one by one, in the order it names them. Positive
# and negative prompts are parts of their own: as one pool, 12 of 12 positives would carry 0 of 3 negatives at 80%.
SCENARIO_PART = "scenario"
TRIGGER_PARTS = {"positive": "positive trigger test", "negative": "negative trigger test"}
GATE_PARTS = (SCENARIO_PART, *TRIGGER_PARTS.values())
TRIGGER_HEADINGS = {"positive": "Triggered on positive prompts", "negative": "Stayed quiet on negative prompts"}
# How the report says a skill or an agent was used, by the kind a log records (evalfile.under_test).
# An agent is used when a run delegates to it or runs it with `kilo run --agent`.
USE_WORDS = {"skill": "was loaded", "agent": "was used"}
# Logs written before they named their checks ran Whitney's own two evals, each scored by one check.
LEGACY_CHECKS = {"agents_md": "no_secret_committed", "kilo_jsonc": "outside_canary_not_reached"}
LEGACY_MINIMUMS = {"agents_md": MIN_ADHERENCE, "kilo_jsonc": MIN_HELD}
GLOBAL_CONFIG_NOTE = "The local user's global Kilo connection config was used but is not included here."
NOT_STAGED_NOTE = (
    "The project held these, but Whitney didn't copy them into the eval projects, so the runs didn't use them. "
    "Where the log records why, the reason follows each name. For `claude-code`, Whitney copies the instruction "
    "files Claude Code reads: `CLAUDE.md` and `.claude/CLAUDE.md`, or `AGENTS.md` and `.claude/AGENTS.md` when "
    "neither `CLAUDE.md` exists, so an `AGENTS.md` beside a `CLAUDE.md` stays out. `CLAUDE.local.md` is personal "
    "and is never copied. It also copies `.claude/skills/` and `.claude/agents/`; "
    "a log from an earlier Whitney names them here."
)
RENAMED_NOTE = (
    "On a case-insensitive disk, such as macOS's default, a file such as `claude.md` counts as `CLAUDE.md` and "
    "is staged under that name. Only the temporary copy is renamed; the project itself is never changed."
)
# The last item of a long not_staged list counts the names left off it (project_files.files_not_staged).
MORE_NOT_STAGED = re.compile(r"and \d+ more")


@dataclass(frozen=True)
class FileInfo:
    """What a log written by whitney.tasks says about the eval file it ran.

    Attributes:
        eval_name: the file's eval name, which the gate's problem lines start with.
        eval_file: the file, relative to the evals path. The report keys its tables by it.
        title: the file's title, or None if it has none.
        source: the file under test, or None in logs that don't record it.
        labels: the score's words for pass, fail and inconclusive.
        checks: each check the file names -> its default minimum pass rate.
        min_pass_rate: the file's own minimum, or None.
        sha256: of the file's bytes as the task read them, or None in logs that don't record it.
        under_test: ("skill" or "agent", its name) if the file tests one, else None.
        problem: what is malformed in the metadata, or None. A file with a
            problem has no checks and pass, fail and inconclusive as its labels.
    """

    eval_name: str
    eval_file: str
    title: str | None
    source: str | None
    labels: tuple[str, str, str]
    checks: Mapping[str, float]
    min_pass_rate: float | None
    sha256: str | None = None
    problem: str | None = None
    under_test: tuple[str, str] | None = None

    @property
    def heading(self) -> str:
        """The name the report gives the file: its title, else its source, else its path."""
        return self.title or self.source or self.eval_file

    def gate_terms(self) -> dict[str, object]:
        """What the gate reads from the log, by the metadata's name for it: logs of one file must agree on these."""
        return {"labels": self.labels, "checks": dict(self.checks), "min_pass_rate": self.min_pass_rate}


@dataclass(frozen=True)
class Record:
    """One scored run of one scenario.

    role and file come from the log's metadata; both are None for a log
    written before it recorded them. trigger is "positive" or "negative" for
    a trigger test, else None. use is "used", "unused" or "unknown" where the
    score says whether the skill or agent under test was used, else None.
    """

    eval_name: str
    variant: str
    scenario: str
    outcome: str
    metadata: dict = field(default_factory=dict, compare=True, hash=False)
    role: str | None = None
    file: FileInfo | None = field(default=None, compare=True, hash=False)
    trigger: str | None = None
    use: str | None = None
    # What each of the eval file's own deny rules refused in this run: (rule, calls, the harness's words or None).
    refusals: tuple[tuple[str, int, str | None], ...] = ()


@dataclass
class Refused:
    """What one of an eval file's own deny rules refused, over every run that recorded it."""

    calls: int = 0  # calls the harness refused under the rule
    runs_refused: int = 0  # runs in which it refused at least one
    runs: int = 0
    said: str | None = None  # the harness's words, from the first refusal


@dataclass
class Cell:
    passed: int = 0
    failed: int = 0
    inconclusive: int = 0

    @property
    def rate(self) -> float | None:
        """passed / (passed + failed), or None if no run was conclusive."""
        conclusive = self.passed + self.failed
        return self.passed / conclusive if conclusive else None


@dataclass
class EvalSummary:
    rows: dict[str, dict[str, Cell]]  # scenario -> variant -> cell
    overall: dict[str, Cell]  # variant -> cell, excluding known gaps
    known_gaps: dict[str, str]  # scenario -> note
    file: FileInfo | None = None  # the first log's; None when every log was written before it was recorded
    roles: dict[str, str] = field(default_factory=dict)  # variant -> role, where the logs record it
    problems: list[str] = field(default_factory=list)  # why the gate can't judge these runs; each fails it
    triggers: dict[str, str] = field(default_factory=dict)  # trigger test -> "positive" or "negative"
    # For a file with trigger tests, what the gate judges: "scenario", "positive trigger test" and "negative
    # trigger test" (GATE_PARTS, in that order) -> variant -> cell, excluding known gaps. Each run is counted in
    # the part its own log puts it in. A part is here if any run was.
    parts: dict[str, dict[str, Cell]] = field(default_factory=dict)
    # In the template's runs of the file's scenarios: how often the skill or agent under test was used ("used",
    # "unused", "unknown" -> runs). Empty when no such run recorded it.
    usage: dict[str, int] = field(default_factory=dict)
    # rule -> what it refused, for each deny rule of the eval file's own for a named skill or agent, in the
    # order the file gave them. Every variant's runs count: the file's permissions are the same in each.
    refused: dict[str, Refused] = field(default_factory=dict)


def records_from_log(log: EvalLog) -> list[Record]:
    """One Record per sample in a log; a sample that errored gets outcome "error".

    The eval, variant and role come from the log's metadata when it names its
    eval file; an older log's come from its task name.
    """
    metadata = getattr(log.eval, "metadata", None) or {}  # a hand-built log in a test may have none
    info = _file_info(metadata)
    if info:
        eval_name, variant, role = info.eval_name, str(metadata.get("variant")), metadata.get("variant_role")
    else:
        (eval_name, variant), role = _split_task_name(log.eval.task), None
    out = []
    for sample in log.samples or []:
        if sample.error or not sample.scores:
            outcome = "error"
        else:
            outcome = next(iter(sample.scores.values())).answer or "error"
        sample_metadata = dict(sample.metadata or {})
        trigger = sample_metadata.get(TRIGGER_KEY)
        out.append(Record(
            eval_name, variant, str(sample.id), outcome, sample_metadata, role, info,
            trigger=trigger if trigger in TRIGGER_KINDS else None, use=_use(sample, info), refusals=_refusals(sample),
        ))
    return out


def _refusals(sample: object) -> tuple[tuple[str, int, str | None], ...]:
    """What the sample's score says each of the eval file's own deny rules refused (tasks._scenario_score).

    Empty for a sample with no score, and for a score that records none or
    records them in any other shape: a count the report can't trust isn't shown.
    """
    if sample.error or not sample.scores:
        return ()
    metadata = getattr(next(iter(sample.scores.values())), "metadata", None)
    recorded = metadata.get("refusals") if isinstance(metadata, dict) else None
    if not isinstance(recorded, list):
        return ()
    out = []
    for item in recorded:
        if not isinstance(item, dict):
            return ()
        rule, calls, said = item.get("rule"), item.get("calls"), item.get("said")
        if not isinstance(rule, str) or type(calls) is not int or calls < 0:
            return ()
        out.append((rule, calls, said if isinstance(said, str) else None))
    return tuple(out)


def _use(sample: object, info: FileInfo | None) -> str | None:
    """Whether a run used the skill or agent its file tests: "used", "unused" or "unknown"; None if the file tests neither.

    A sample that errored or has no score is "unknown", so the count of runs
    matches the table's. So is a score that doesn't say, or says anything but true or false.
    """
    if info is None or info.under_test is None:
        return None
    if sample.error or not sample.scores:
        return "unknown"
    metadata = getattr(next(iter(sample.scores.values())), "metadata", None)
    used = metadata.get("used") if isinstance(metadata, dict) else None
    return "used" if used is True else "unused" if used is False else "unknown"


def _file_info(metadata: dict) -> FileInfo | None:
    """The eval file a log's metadata describes, or None for a log written before it did.

    Metadata the gate can't read becomes a FileInfo with a problem, not an
    exception, so the report still renders and the gate fails that file.
    """
    if "eval_file" not in metadata:
        return None
    problem = _metadata_problem(metadata)
    eval_file = str(metadata["eval_file"])
    named = {
        "eval_name": _text(metadata.get("eval_name")) or eval_file,
        "eval_file": eval_file,
        "title": _text(metadata.get("title")),
        "source": _text(metadata.get("source")),
        "sha256": _text(metadata.get("eval_file_sha256")),
    }
    if problem:
        return FileInfo(**named, labels=GENERIC_LABELS, checks={}, min_pass_rate=None, problem=problem)
    return FileInfo(
        **named,
        under_test=_under_test(metadata.get("under_test")),
        labels=tuple(metadata["labels"]),
        checks={name: c["min_pass_rate"] for name, c in metadata["checks"].items()},
        min_pass_rate=metadata.get("min_pass_rate"),
    )


def _under_test(recorded: object) -> tuple[str, str] | None:
    """The skill or agent a log says its file tests, or None if it names none the report knows."""
    if not isinstance(recorded, dict) or recorded.get("kind") not in USE_WORDS or not _text(recorded.get("name")):
        return None
    return recorded["kind"], recorded["name"]


def _metadata_problem(metadata: dict) -> str | None:
    """What in a log's eval-file metadata the report and the gate can't read, or None."""
    for key in ("eval_file", "variant"):
        if not _text(metadata.get(key)):
            return f"{key} is {metadata.get(key)!r}, not a name"
    labels = metadata.get("labels")
    if not isinstance(labels, list | tuple) or len(labels) != 3 or not all(map(_text, labels)) or len(set(labels)) != 3:
        return f"labels is {labels!r}, not three different words"
    checks = metadata.get("checks")
    if not isinstance(checks, dict):
        return f"checks is {checks!r}, not each check's minimum by name"
    for name, check in checks.items():
        if not isinstance(check, dict):
            return f"check {name} is {check!r}, not its labels, judges and min_pass_rate"
        if check.get("judges") not in JUDGES:
            return f"check {name} judges {check.get('judges')!r}, not agent or harness"
        if not _is_rate(check.get("min_pass_rate")):
            return f"check {name}'s min_pass_rate is {check.get('min_pass_rate')!r}, not a number from 0 to 1"
    rate = metadata.get("min_pass_rate")
    if rate is not None and not _is_rate(rate):
        return f"min_pass_rate is {rate!r}, not a number from 0 to 1"
    if not checks and rate is None:
        return "it names no check and no min_pass_rate"
    return None


def _text(value: object) -> str | None:
    """`value` if it is a string other than "", else None."""
    return value if isinstance(value, str) and value else None


def _is_rate(value: object) -> bool:
    """A number from 0 to 1. Not a bool, though True is an int, and not nan, which compares false to both bounds."""
    return not isinstance(value, bool) and isinstance(value, int | float) and 0 <= value <= 1


def _split_task_name(task: str) -> tuple[str, str]:
    """'agents_md_template' -> ('agents_md', 'template')."""
    for name in EVALS:
        if task.startswith(f"{name}_"):
            return name, task.removeprefix(f"{name}_")
    raise ValueError(f"unknown eval task {task!r}; expected one of {list(EVALS)} plus a variant")


def _legacy_eval(task: str) -> str | None:
    """The shipped eval an older log's task ran, or None if it ran another (ifevalcode, say)."""
    return next((name for name in EVALS if task.startswith(f"{name}_")), None)


def summarise(records: list[Record]) -> dict[str, EvalSummary]:
    """Count outcomes per eval file, scenario and variant.

    Keyed by eval file for logs that name it, else by eval name. Each run is
    counted in its own log's words for pass and fail. Logs of one file that
    record different labels, checks or min_pass_rate, or whose metadata is
    malformed, give that file a problem, which fails the gate.
    """
    rows: dict[str, dict[str, dict[str, Cell]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(Cell)))
    overall: dict[str, dict[str, Cell]] = defaultdict(lambda: defaultdict(Cell))
    gaps: dict[str, dict[str, str]] = defaultdict(dict)
    files: dict[str, FileInfo] = {}
    differ: dict[str, set[str]] = defaultdict(set)
    malformed: dict[str, list[str]] = defaultdict(list)
    roles: dict[str, dict[str, str]] = defaultdict(dict)
    triggers: dict[str, dict[str, str]] = defaultdict(dict)
    usage: dict[str, dict[str, int]] = defaultdict(dict)
    refused: dict[str, dict[str, Refused]] = defaultdict(dict)
    parts: dict[str, dict[str, dict[str, Cell]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(Cell)))
    for r in records:
        key = r.file.eval_file if r.file else r.eval_name
        if r.file:
            first = files.setdefault(key, r.file).gate_terms()
            own = r.file.gate_terms()
            differ[key].update(term for term in first if first[term] != own[term])
            if r.file.problem and r.file.problem not in malformed[key]:
                malformed[key].append(r.file.problem)
        if r.role:
            roles[key][r.variant] = r.role
        if r.trigger:
            triggers[key][r.scenario] = r.trigger
        elif r.use and r.variant == TEMPLATE_VARIANT:
            usage[key][r.use] = usage[key].get(r.use, 0) + 1
        for rule, calls, said in r.refusals:
            total = refused[key].setdefault(rule, Refused())
            total.calls += calls
            total.runs_refused += calls > 0
            total.runs += 1
            total.said = total.said or said
        passing, failing = (r.file.labels if r.file else EVALS[r.eval_name])[:2]
        if r.metadata.get("known_gap"):
            gaps[key][r.scenario] = r.metadata["known_gap"]
        targets = [rows[key][r.scenario][r.variant]]
        part = parts[key][TRIGGER_PARTS[r.trigger] if r.trigger else SCENARIO_PART]
        if not r.metadata.get("known_gap"):
            targets += [overall[key][r.variant], part[r.variant]]
        for cell in targets:
            if r.outcome == passing:
                cell.passed += 1
            elif r.outcome == failing:
                cell.failed += 1
            else:
                cell.inconclusive += 1
    return {
        key: EvalSummary(
            rows={s: dict(v) for s, v in rows[key].items()},
            overall=dict(overall[key]),
            known_gaps=gaps[key],
            file=files.get(key),
            roles=roles[key],
            problems=_problems(differ[key], malformed[key]),
            triggers=triggers[key],
            usage=usage[key],
            refused=refused[key],
            parts={name: dict(parts[key][name]) for name in GATE_PARTS if name in parts[key]} if triggers[key] else {},
        )
        for key in rows
    }


def _problems(differ: set[str], malformed: list[str]) -> list[str]:
    """The problem lines for an eval file whose logs record different gate terms or malformed metadata."""
    problems = [f"its log metadata is malformed: {problem}, so its runs can't be gated" for problem in malformed]
    terms = [term for term in ("labels", "checks", "min_pass_rate") if term in differ]
    if terms:
        listed = terms[0] if len(terms) == 1 else ", ".join(terms[:-1]) + " and " + terms[-1]
        problems.append(f"its logs record different {listed}, so its runs can't be gated as one eval file")
    return problems


def replaced_minimums(
    min_adherence: float | None = None, min_held: float | None = None, min_rates: Mapping[str, float] | None = None
) -> dict[str, float]:
    """Check -> the minimum pass rate replacing its default for this run.

    `min_adherence` and `min_held` are the deprecated --min-adherence and
    --min-held: aliases for `min_rates` on no_secret_committed and
    outside_canary_not_reached, the checks agents_md and kilo_jsonc score
    with. A rate in `min_rates` (--min-rate) wins over an alias for the same check.
    """
    rates = {}
    if min_adherence is not None:
        rates[LEGACY_CHECKS["agents_md"]] = min_adherence
    if min_held is not None:
        rates[LEGACY_CHECKS["kilo_jsonc"]] = min_held
    rates.update(min_rates or {})
    return rates


def file_minimum(info: FileInfo, min_rates: Mapping[str, float] | None = None) -> float:
    """The minimum pass rate an eval file's template must reach.

    The highest of its checks' minimums, or the file's own min_pass_rate if
    that is higher: a file can raise its bar, never lower it. A check's
    minimum is `min_rates[check]` if given (--min-rate), else the default it declares.
    """
    rates = min_rates or {}
    minimums = [rates.get(name, default) for name, default in info.checks.items()]
    if info.min_pass_rate is not None:
        minimums.append(info.min_pass_rate)
    return max(minimums)


def check_thresholds(
    summary: dict[str, EvalSummary],
    min_adherence: float | None = None,
    min_held: float | None = None,
    min_conclusive: float = MIN_CONCLUSIVE,
    min_rates: Mapping[str, float] | None = None,
) -> list[str]:
    """Problems, if the template variant falls short for any eval present.

    Two bars: the overall rate must reach its minimum, and at
    least `min_conclusive` of the template's runs must be conclusive. The
    rate leaves inconclusive runs out, so without the second bar one followed
    run among many that ended early (an outage, a spent credit balance) would
    pass at 100%. Errored and not-attempted runs count as not conclusive.
    Only the template counts: baselines and controls never affect the gate.

    A file with trigger tests must clear both bars once for each part it
    has: its scenarios, its positive trigger tests and its negative trigger
    tests. Read together, trigger tests that all pass would carry a skill the
    agent loads and then doesn't follow, and positive prompts that all pass
    would carry negative prompts that don't. A problem names the part that
    fell short.

    A file's minimum is file_minimum's, from the checks its logs record, with
    the rates replaced_minimums gives. Logs written before they recorded them
    are gated by the check their eval was scored with: agents_md by
    no_secret_committed (MIN_ADHERENCE unless replaced) and kilo_jsonc by
    outside_canary_not_reached (MIN_HELD). A file with a problem (see
    summarise) fails with it, whatever its rate.
    """
    rates = replaced_minimums(min_adherence, min_held, min_rates)
    problems = []
    for key, eval_summary in summary.items():
        name = eval_summary.file.eval_name if eval_summary.file else key
        if eval_summary.problems:
            problems.extend(f"{name}: {problem}" for problem in eval_summary.problems)
            continue
        if eval_summary.file:
            minimum = file_minimum(eval_summary.file, rates)
        else:
            minimum = rates.get(LEGACY_CHECKS[key], LEGACY_MINIMUMS[key])
        for part, cell in _gated_parts(eval_summary):
            of_part, part_runs = (f"{part}s ", f"{part} runs") if part else ("", "runs")
            rate = cell.rate
            if part and rate is None:
                problems.append(f"{name}: template {part}s had no conclusive runs, so they are below the minimum {minimum:.0%}")
            elif rate is None or rate < minimum:
                shown = "no conclusive runs" if rate is None else f"{rate:.0%}"
                problems.append(f"{name}: template {of_part}{shown} is below the minimum {minimum:.0%}")
            conclusive = cell.passed + cell.failed
            runs = conclusive + cell.inconclusive
            if conclusive and conclusive / runs < min_conclusive:  # no conclusive runs at all is reported above
                problems.append(
                    f"{name}: only {conclusive}/{runs} template {part_runs} were conclusive, "
                    f"below the minimum {min_conclusive:.0%}"
                )
    return problems


def _gated_parts(s: EvalSummary) -> list[tuple[str, Cell]]:
    """The template's counts the gate judges for one eval file, each with its name in a problem line.

    One part, unnamed, for a file without trigger tests. A file with them has
    "scenario", "positive trigger test" and "negative trigger test", each
    judged alone, and only those it has runs of: a file with no scenarios, or
    with one kind of prompt, has fewer. A part whose runs were all known
    gaps, or all in other variants, is still judged, and fails for want of
    conclusive runs, as a file without trigger tests does.
    """
    if not s.parts:
        return [("", s.overall.get(TEMPLATE_VARIANT, Cell()))]
    return [(part, cells.get(TEMPLATE_VARIANT, Cell())) for part, cells in s.parts.items()]


def render_markdown(summary: dict[str, EvalSummary]) -> str:
    """Results as Markdown tables, one per eval file.

    Each is headed with the file's title and its rate in its own words. Its
    variants are in role order (baselines, template, controls; by name within
    a role), with one Δ column per variant other than the template. A file
    run in the template only says why it has no Δ, a control variant that
    never failed is named below the table, and so is each problem that fails
    the file at the gate. An older log's eval shows the two variants it
    always ran, as it always did, without those notes.

    A file that tests a skill or an agent says in how many of the template's
    scenario runs it was used. Its trigger tests get a table each for the
    positive and the negative prompts, with the variants those ran in, and a
    line giving each of the template's rates the gate reads: over its
    scenarios, over its positive trigger tests and over its negative ones.
    """
    parts = []
    for key, s in summary.items():
        passing, failing, neither = s.file.labels if s.file else EVALS[key][:3]
        neither = neither.replace("_", " ")
        title = _escaped(s.file.heading if s.file else LEGACY_TITLES[key])
        scenarios = {name: cells for name, cells in s.rows.items() if name not in s.triggers}
        variants = _variants(s, scenarios) if s.file else EVALS[key][3]
        overall = _overall(scenarios, s.known_gaps) if s.triggers else s.overall
        parts.append(f"## {title}: {passing} / ({passing} + {failing})\n")
        parts.extend(_table("Scenario", scenarios, overall, variants, neither, s.known_gaps))
        if s.known_gaps:
            parts.append("\nKnown gaps, excluded from overall and thresholds:")
            parts.extend(f"- {scenario}: {note}" for scenario, note in sorted(s.known_gaps.items()))
        if not [v for v in variants if v != TEMPLATE_VARIANT]:
            parts.append(f"\nOnly `{TEMPLATE_VARIANT}` ran for this file, so there is no Δ.")
        if s.usage and s.file and s.file.under_test:
            parts.append(f"\n{_usage_note(s.usage, *s.file.under_test)}")
        parts.extend(f"\n{_refused_note(rule, total)}" for rule, total in s.refused.items())
        for kind in TRIGGER_KINDS:
            prompts = {name: cells for name, cells in s.rows.items() if s.triggers.get(name) == kind}
            if prompts:
                parts.append(f"\n### {TRIGGER_HEADINGS[kind]}\n")
                parts.extend(_table("Prompt", prompts, _overall(prompts, {}), _variants(s, prompts), neither, {}))
        if s.triggers:
            parts.append(f"\n{_gate_note(_gated_parts(s), neither)}")
        parts.extend(f"\n{warning}" for warning in _control_warnings(s, _variants(s, s.rows) if s.file else variants))
        parts.extend(f"\nThe gate fails this file: {problem}." for problem in s.problems)
        parts.append("")
    return "\n".join(parts)


def _gate_note(gated: list[tuple[str, Cell]], neither: str) -> str:
    """The line under a skill's or an agent's tables giving the template's counts the gate reads, part by part."""
    counts = [
        (f"{part}s", _cell(cell, neither) if cell.passed + cell.failed + cell.inconclusive else "no runs")
        for part, cell in gated
    ]
    if len(counts) == 1:
        [(part, count)] = counts
        return f"The gate reads the template's {part}: {count}."
    first, *middle, last = (part for part, _ in counts)
    named = "".join([first, *(f", its {part}" for part in middle), f" and its {last}"])
    read = ", ".join(f"{part} {count}" for part, count in counts)
    return f"The gate reads the template's {named} separately: {read}."


def _table(
    heading: str, rows: dict[str, dict[str, Cell]], overall: dict[str, Cell], variants: tuple[str, ...], neither: str,
    known_gaps: dict[str, str],
) -> list[str]:
    """A Markdown table of `rows` by variant, with a Δ column per variant other than the template and an overall row."""
    others = [v for v in variants if v != TEMPLATE_VARIANT]
    deltas = ["Δ"] if len(others) == 1 else [f"Δ {v}" for v in others]
    lines = [f"| {heading} | " + " | ".join([*variants, *deltas]) + " |", "|---" * (1 + len(variants) + len(deltas)) + "|"]
    for name, cells in sorted(rows.items()):
        label = f"{name} (known gap)" if name in known_gaps else name
        lines.append(_row(label, cells, variants, others, neither))
    lines.append(_row("**overall**", overall, variants, others, neither))
    return lines


def _overall(rows: dict[str, dict[str, Cell]], known_gaps: dict[str, str]) -> dict[str, Cell]:
    """Each variant's counts over `rows`, leaving out the known gaps."""
    overall: dict[str, Cell] = defaultdict(Cell)
    for name, cells in rows.items():
        if name in known_gaps:
            continue
        for variant, cell in cells.items():
            overall[variant].passed += cell.passed
            overall[variant].failed += cell.failed
            overall[variant].inconclusive += cell.inconclusive
    return dict(overall)


def _usage_note(usage: dict[str, int], kind: str, name: str) -> str:
    """In how many of the template's scenario runs the skill or agent under test was used.

    A scenario that passed in a run where the skill never loaded shows the
    model's default, not the skill, so the count belongs beside the table.
    """
    used, unused, unknown = (usage.get(word, 0) for word in ("used", "unused", "unknown"))
    runs = used + unused + unknown
    return (
        f"In the template's {runs} scenario {'run' if runs == 1 else 'runs'}, the {kind} `{_escaped(name)}` "
        f"{USE_WORDS[kind]} in {used}, wasn't in {unused} and couldn't be told in {unknown}."
    )


SAID_LIMIT = 200  # characters of the harness's words kept in a refusal's line


def _refused_note(rule: str, total: Refused) -> str:
    """What one of the eval file's own deny rules refused, in the harness's words.

    A rule that refused nothing is said to have shown nothing: no refusal
    was read, whether or not the model called what it switches off, so
    these runs are no proof it works.
    """
    name = " ".join(rule.split()).replace("`", "'")  # one line, and nothing that closes the code span early
    runs = f"{total.runs} {'run' if total.runs == 1 else 'runs'}"
    if not total.calls:
        shown = "this" if total.runs == 1 else "these"
        verb = "doesn't" if total.runs == 1 else "don't"
        return f"The eval file switched off `{name}`. No call was refused under it, so {shown} {runs} {verb} show the rule refusing anything."
    said = " ".join((total.said or "").split())
    said = said if len(said) <= SAID_LIMIT else said[:SAID_LIMIT] + "…"
    calls = f"{total.calls} {'call' if total.calls == 1 else 'calls'}"
    return f'The eval file switched off `{name}`. The harness refused {calls} to it, in {total.runs_refused} of {runs}: "{said}"'


def _escaped(text: str) -> str:
    """`text` with each `|` escaped, so a title or path can't split a Markdown table's cells."""
    return text.replace("|", "\\|")


def _variants(s: EvalSummary, rows: dict[str, dict[str, Cell]]) -> tuple[str, ...]:
    """The variants `rows` of an eval file's logs ran in, in role order and by name within a role."""
    seen = {variant for cells in rows.values() for variant in cells}
    rank = {role: index for index, role in enumerate(ROLE_ORDER)}
    return tuple(sorted(seen, key=lambda v: (rank.get(s.roles.get(v), len(ROLE_ORDER)), v)))


def _control_warnings(s: EvalSummary, variants: tuple[str, ...]) -> list[str]:
    """One line per control variant with runs outside the known gaps and no failing run among them.

    Only conclusive runs count: a control that never failed in them didn't
    show its checks can fail, so a passing template may not show the rule
    holding; one with no conclusive run can't show it at all. Neither fails
    the gate: with a few runs a control can pass by chance, and a gate on it
    would make CI flaky.
    """
    warnings = []
    for variant in variants:
        cell = s.overall.get(variant)
        if s.roles.get(variant) != "control" or cell is None or cell.failed:
            continue
        if cell.passed:
            plural = "run" if cell.passed == 1 else "runs"
            warnings.append(
                f"`{variant}` never failed in {cell.passed} conclusive {plural}, "
                "so the template's result may not show the rule holding."
            )
        else:
            warnings.append(f"`{variant}` had no conclusive run, so it can't show the check can fail.")
    return warnings


def render_header(
    logs: list[EvalLog], evals_path: str | Path | None = None, min_rates: Mapping[str, float] | None = None
) -> str:
    """The report's lines after its target and model: what an auditor needs to tie it to the scenarios.

    For logs that name their eval file: the Whitney and harness versions they
    recorded (every value, if they differ; "not recorded" if none did) and
    `evals_path`, then one row per eval file and recorded SHA-256, with its
    title. A recorded SHA-256 is shown with a note when the file under
    `evals_path` now differs or is gone; a log that recorded none shows the
    file's SHA-256 as it is now. Then each minimum `min_rates` replaced for
    this run, noting any no eval file in the logs uses. Empty when there is
    none of these, so logs written before them render as before.
    """
    files: dict[tuple[str, str | None], FileInfo] = {}  # by eval file and recorded SHA-256
    used: set[str] = set()
    recorded: dict[str, set[str]] = {key: set() for key in ("whitney_version", "harness_version")}
    for log in logs:
        metadata = getattr(log.eval, "metadata", None) or {}
        info = _file_info(metadata)
        if info is None:
            legacy = _legacy_eval(log.eval.task)
            used.update([LEGACY_CHECKS[legacy]] if legacy else [])
            continue
        used.update(info.checks)
        files.setdefault((info.eval_file, info.sha256), info)
        for key, values in recorded.items():
            if metadata.get(key):
                values.add(str(metadata[key]))
    lines = []
    if files:
        shown = {key: ", ".join(f"`{v}`" for v in sorted(values)) or NOT_RECORDED for key, values in recorded.items()}
        evals = f"`{evals_path}`" if evals_path else NOT_GIVEN
        lines = [
            f"Whitney: {shown['whitney_version']} · Harness: {shown['harness_version']} · Evals: {evals}",
            "",
            "| Eval file | Title | SHA-256 |",
            "|---|---|---|",
        ]
        lines += [
            f"| `{_escaped(path)}` | {_escaped(info.heading)} | {_sha256_shown(evals_path, info)} |"
            # By path only; a file's rows stay in the order its logs came, since sorting can't compare a hash with None.
            for (path, _), info in sorted(files.items(), key=lambda item: item[0][0])
        ]
        lines.append("")
    if min_rates:
        replaced = ", ".join(
            f"`{name}={value:g}`" + ("" if name in used else " (no eval file in these logs uses it)")
            for name, value in sorted(min_rates.items())
        )
        lines += [f"Minimum pass rates replaced for this run: {replaced}.", ""]
    return "\n".join(lines) + "\n" if lines else ""


def _sha256_shown(evals_path: str | Path | None, info: FileInfo) -> str:
    """The header's SHA-256 cell: the recorded hash, noting a file now changed or gone; else the file's hash now."""
    current = _current_sha256(evals_path, info.eval_file)
    if info.sha256 is None:
        return NOT_GIVEN if current is None else (f"`{current}`" if current else "not found")
    shown = f"`{info.sha256}`"
    if current is None or current == info.sha256:
        return shown
    return f"{shown} (not found)" if not current else f"{shown} (changed since the run)"


def _current_sha256(evals_path: str | Path | None, eval_file: str) -> str | None:
    """The eval file's SHA-256 under `evals_path` now: None if no path was given, "" if the file isn't there."""
    if not evals_path:
        return None
    path = Path(evals_path) / eval_file
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def render_ifevalcode_markdown(logs: list[EvalLog]) -> str:
    """Compare official IFEvalCode correctness/adherence scores by template variant."""
    metrics = {
        "both_passed": "Overall accuracy",
        "correctness": "Correctness",
        "instruction": "Instruction adherence",
    }
    totals = {
        variant: {key: [0.0, 0] for key in metrics}
        for variant in ("baseline", "template")
    }
    column_removed = set()
    candidate_counts = set()
    scored_counts = set()
    shuffle_seeds = set()
    limits = set()
    epoch_counts = set()
    for log in logs:
        variant = log.eval.task.removeprefix("ifevalcode_")
        if variant not in totals:
            continue
        metadata = log.eval.metadata or {}
        if "chinese_question_column_removed" in metadata:
            column_removed.add(bool(metadata["chinese_question_column_removed"]))
        candidate_count = metadata.get("candidate_records", metadata.get("samples_selected"))
        if candidate_count is not None:
            candidate_counts.add(int(candidate_count))
        scored_counts.add(len(log.samples or []))
        config = log.eval.config or {}
        shuffle_seed = config.get("sample_shuffle") if isinstance(config, dict) else getattr(config, "sample_shuffle", None)
        limit = config.get("limit") if isinstance(config, dict) else getattr(config, "limit", None)
        epochs = config.get("epochs") if isinstance(config, dict) else getattr(config, "epochs", None)
        if shuffle_seed is not None:
            shuffle_seeds.add(shuffle_seed)
        if limit is not None:
            limits.add(int(limit))
        if epochs is not None:
            epoch_counts.add(int(epochs))
        for sample in log.samples or []:
            if sample.error or not sample.scores:
                continue
            for score in sample.scores.values():
                if not isinstance(score.value, dict):
                    continue
                for key in metrics:
                    if key in score.value:
                        totals[variant][key][0] += float(score.value[key])
                        totals[variant][key][1] += 1

    def format_rate(total: list[float | int]) -> str:
        passed, count = total
        return "—" if not count else f"{passed / count:.1%} ({int(passed)}/{count})"

    lines = [
        "## IFEvalCode: template interference check",
        "",
        "Dataset: local sanitised IFEvalCode JSONL.",
        f"Chinese question column removed: {'yes' if True in column_removed else 'unknown'}.",
        f"Candidate records before Inspect limits: {max(candidate_counts) if candidate_counts else 'unknown'}.",
        f"Scored samples per variant (total): {max(scored_counts) if scored_counts else 'unknown'}.",
        f"Limit per variant: {max(limits) if limits else 'none'}; epochs: {max(epoch_counts) if epoch_counts else 'unknown'}.",
        f"Shuffle seed: {', '.join(str(seed) for seed in sorted(shuffle_seeds, key=str)) if shuffle_seeds else 'not used'}.",
        "",
        "| Metric | baseline | template | Δ (percentage points) |",
        "|---|---:|---:|---:|",
    ]
    for key, label in metrics.items():
        baseline = totals["baseline"][key]
        template = totals["template"][key]
        delta = "—"
        if baseline[1] and template[1]:
            delta = f"{(template[0] / template[1] - baseline[0] / baseline[1]) * 100:+.1f}"
        lines.append(f"| {label} | {format_rate(baseline)} | {format_rate(template)} | {delta} |")
    return "\n".join(lines) + "\n"


def render_harbor_markdown(logs: list[EvalLog]) -> str:
    """Compare Harbor reward and pass rates for baseline/template variants."""
    totals = {
        variant: {"reward": [0.0, 0], "passed": [0, 0]}
        for variant in ("baseline", "template")
    }
    for log in logs:
        variant = log.eval.task.removeprefix("bigcodebench_hard_complete_")
        if variant not in totals:
            continue
        for sample in log.samples or []:
            if sample.error or not sample.scores:
                continue
            for score in sample.scores.values():
                try:
                    reward = float(score.value)
                except (TypeError, ValueError):
                    continue
                totals[variant]["reward"][0] += reward
                totals[variant]["reward"][1] += 1
                totals[variant]["passed"][0] += int(reward > 0)
                totals[variant]["passed"][1] += 1

    def mean_reward(values: list[float | int]) -> str:
        total, count = values
        return "—" if not count else f"{total / count:.1%}"

    def pass_rate(values: list[float | int]) -> str:
        passed, count = values
        return "—" if not count else f"{passed / count:.1%} ({int(passed)}/{count})"

    baseline_reward = totals["baseline"]["reward"]
    template_reward = totals["template"]["reward"]
    delta = "—"
    if baseline_reward[1] and template_reward[1]:
        delta = f"{(template_reward[0] / template_reward[1] - baseline_reward[0] / baseline_reward[1]) * 100:+.1f}"

    lines = [
        "## BigCodeBench-Hard Complete (Harbor)",
        "",
        "Scores are Harbor verifier rewards; pass rate counts reward values above zero.",
        "",
        "| Metric | baseline | template | Δ (percentage points) |",
        "|---|---:|---:|---:|",
        f"| Mean reward | {mean_reward(baseline_reward)} | {mean_reward(template_reward)} | {delta} |",
        f"| Pass rate | {pass_rate(totals['baseline']['passed'])} | {pass_rate(totals['template']['passed'])} | — |",
    ]
    return "\n".join(lines) + "\n"


def _not_staged_name(path: str, reasons: dict[str, str]) -> str:
    if MORE_NOT_STAGED.fullmatch(path):
        return path
    return f"`{path}` ({reasons[path]})" if path in reasons else f"`{path}`"


def render_asset_provenance_markdown(logs: list[EvalLog]) -> str:
    """Include each staged policy/config copy once and map it to its variants.

    Project files staged under another name (the `renamed` metadata, see
    whitney.project_files.files_renamed) and project paths a variant's
    loader left out (the `not_staged` metadata, see
    whitney.project_files.files_not_staged, each with its reason from
    `not_staged_reasons` when recorded) are named after the variant
    inputs, so a reader knows what the runs saw. Logs without those keys
    render as before.
    """
    task_inputs: dict[str, dict[str, object]] = {}
    file_copies: dict[tuple[str, str], set[str]] = defaultdict(set)
    # (the overlay as JSON, the default overlay's keys left out of it) -> the tasks that ran with it
    permission_overlays: dict[tuple[str, tuple[str, ...]], set[str]] = defaultdict(set)
    prompt_preambles: dict[str, set[str]] = defaultdict(set)
    not_staged: dict[str, list[str]] = {}
    not_staged_reasons: dict[str, dict[str, str]] = {}
    renamed: dict[str, dict[str, str]] = {}
    for log in logs:
        metadata = log.eval.metadata or {}
        assets = metadata.get("evaluation_assets")
        if not isinstance(assets, dict):
            continue
        task = log.eval.task
        model = metadata.get("model") or metadata.get("kilo_model", "unknown")  # kilo_model: logs before the rename
        target = metadata.get("target", "kilo")  # logs before targets were all Kilo
        execution = metadata.get("execution", "unknown")
        source = metadata.get("asset_source", "unknown")
        paths = sorted(assets)
        task_inputs[task] = {
            "source": source,
            "target": target,
            "model": model,
            "model_access": _model_access_label(execution, metadata.get("model_access"), metadata.get("kilo_cli_model")),
            "execution": execution,
            "paths": paths,
        }
        for path, contents in assets.items():
            file_copies[(str(path), str(contents))].add(task)
        harness_permissions = metadata.get("harness_permissions")
        if harness_permissions is not None:
            overlay = json.dumps(harness_permissions, indent=2, sort_keys=True)
            left_out = tuple(sorted(metadata.get("harness_permissions_left_out") or []))  # absent from older logs
            permission_overlays[(overlay, left_out)].add(task)
        preamble = metadata.get("evaluation_preamble")
        if preamble:
            prompt_preambles[str(preamble)].add(task)
        if metadata.get("renamed"):  # absent from logs written before it was recorded
            renamed[task] = dict(metadata["renamed"])
        left_out = metadata.get("not_staged")  # absent from logs written before it was recorded
        if left_out:
            not_staged[task] = list(left_out)  # in files_not_staged's order, with any "and N more" last
            not_staged_reasons[task] = dict(metadata.get("not_staged_reasons") or {})  # also absent from older logs

    if not task_inputs:
        return ""

    sections = [
        "## Evaluation Instructions and Assets",
        "",
        GLOBAL_CONFIG_NOTE
        if all(details["model_access"] == "global Kilo config" for details in task_inputs.values())
        else "Provider connection settings and API keys are not included here; Model access names where they came from.",
        "",
        "### Variant Inputs",
        "",
        "| Eval variant | Target | Asset source | Model | Model access | Execution | Staged files |",
        "|---|---|---|---|---|---|---|",
    ]
    for task, details in sorted(task_inputs.items()):
        paths = ", ".join(f"`{path}`" for path in details["paths"]) or "none"
        sections.append(
            f"| `{task}` | {details['target']} | {details['source']} | `{details['model']}` | {details['model_access']} "
            f"| {details['execution']} | {paths} |"
        )

    if renamed:
        sections.extend(["", "### Renamed for the Eval", "", RENAMED_NOTE, "", "| Eval variant | Renamed |", "|---|---|"])
        for task, names in sorted(renamed.items()):
            pairs = ", ".join(f"`{original}` staged as `{staged}`" for original, staged in sorted(names.items()))
            sections.append(f"| `{task}` | {pairs} |")

    if not_staged:
        sections.extend(["", "### Not Staged", "", NOT_STAGED_NOTE, "", "| Eval variant | Not staged |", "|---|---|"])
        for task, paths in sorted(not_staged.items()):
            names = (_not_staged_name(path, not_staged_reasons[task]) for path in paths)
            sections.append(f"| `{task}` | {', '.join(names)} |")

    sections.extend(["", "### File Snapshots", ""])
    for path in sorted({path for path, _ in file_copies}):
        copies = [(contents, users) for (copy_path, contents), users in file_copies.items() if copy_path == path]
        for index, (contents, users) in enumerate(sorted(copies, key=lambda copy: sorted(copy[1])), 1):
            label = f" (copy {index})" if len(copies) > 1 else ""
            sections.extend([f"#### `{path}`{label}", f"Used by: {', '.join(f'`{task}`' for task in sorted(users))}.", ""])
            fence_length = max((len(run) for run in re.findall(r"`+", contents)), default=2) + 1
            fence = "`" * max(3, fence_length)
            language = {
                ".md": "markdown",
                ".json": "json",
                ".jsonc": "jsonc",
                ".py": "python",
                ".yml": "yaml",
                ".yaml": "yaml",
            }.get(Path(path).suffix.lower(), "text")
            sections.extend([f"{fence}{language}", contents.rstrip(), fence, ""])

    if permission_overlays:
        sections.extend(["### Harness Permission Overlays", ""])
        for (overlay, left_out), users in sorted(permission_overlays.items(), key=lambda item: sorted(item[1])):
            sections.extend([f"Used by: {', '.join(f'`{task}`' for task in sorted(users))}.", "", "```json", overlay, "```", ""])
            for key in left_out:
                sections.extend([(
                    f"Whitney's default overlay also allows `{key}`. It is left out here, "
                    "because a check in the file tests the project's own rule for it."
                ), ""])

    if prompt_preambles:
        sections.extend(["### Evaluation Prompt Preambles", ""])
        for preamble, users in sorted(prompt_preambles.items(), key=lambda item: sorted(item[1])):
            sections.extend([f"Used by: {', '.join(f'`{task}`' for task in sorted(users))}.", "", "```text", preamble.rstrip(), "```", ""])

    return "\n".join(sections).rstrip() + "\n"


def _model_access_label(execution: str, access: dict | None, kilo_cli_model: str | None = None) -> str:
    """Where the harness's model connection came from, for the report; never the key itself.

    Docker runs are always served by the eval's Inspect model through the
    bridge, with the name Kilo was told alongside when it was recorded.
    Local runs use the named API key if one was given, else the global Kilo
    config (also true of local logs written before model_access was recorded).
    """
    if execution == "docker":
        label = "Inspect model (Docker bridge)"
        return label + (f"; Kilo told `{kilo_cli_model}`" if kilo_cli_model else "")
    if access and access.get("api_key_env"):
        label = f"key from `{access['api_key_env']}`"
        return label + (f", base URL `{access['base_url']}`" if access.get("base_url") else "")
    if execution == "local":
        return "global Kilo config"
    return "unknown"


def _row(label: str, cells: dict[str, Cell], variants: tuple[str, ...], others: list[str], neither: str) -> str:
    shown = [_cell(cells.get(v), neither) for v in variants] + [_delta(cells, other) for other in others]
    return f"| {label} | " + " | ".join(shown) + " |"


def _cell(cell: Cell | None, neither: str) -> str:
    if cell is None:
        return "—"
    text = f"{cell.passed}/{cell.passed + cell.failed}"
    return f"{text} ({cell.inconclusive} {neither})" if cell.inconclusive else text


def _delta(cells: dict[str, Cell], other: str) -> str:
    """Template minus the other variant, in percentage points."""
    a = cells.get(TEMPLATE_VARIANT, Cell()).rate
    b = cells.get(other, Cell()).rate
    return "—" if a is None or b is None else f"{(a - b) * 100:+.0f}%"
