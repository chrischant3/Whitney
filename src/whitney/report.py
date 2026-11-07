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
GLOBAL_CONFIG_NOTE = "The local user's global Kilo connection config was used but is not included here."
NOT_STAGED_NOTE = (
    "The project held these, but Whitney didn't copy them into the eval projects, so the runs didn't use them. "
    "Where the log records why, the reason follows each name. For `claude-code`, Whitney copies one instruction "
    "file, as `AGENTS.md`. A `CLAUDE.md` beside `AGENTS.md` stays out because Whitney's `claude-code` evals and "
    "their baseline (the same run with `AGENTS.md` removed) are about `AGENTS.md`, and in one run Claude Code "
    "ignored `AGENTS.md` once `.claude/CLAUDE.md` existed."
)
RENAMED_NOTE = (
    "Whitney's `claude-code` evals and their baseline (the same run with `AGENTS.md` removed) are about "
    "`AGENTS.md`, so a project with no usable `AGENTS.md` has its `CLAUDE.md` staged under that name. Only the "
    "temporary copy is renamed; the project itself is never changed."
)
# The last item of a long not_staged list counts the names left off it (project_files.files_not_staged).
MORE_NOT_STAGED = re.compile(r"and \d+ more")


@dataclass(frozen=True)
class CheckMinimum:
    """What the gate needs to know about one check an eval file names.

    Attributes:
        judges: "agent" or "harness" (whitney.checks.CheckSpec.judges).
        min_pass_rate: the check's default minimum pass rate.
    """

    judges: str
    min_pass_rate: float


@dataclass(frozen=True)
class FileInfo:
    """What a log written by whitney.tasks says about the eval file it ran.

    Attributes:
        eval_file: the file, relative to the evals path.
        title: the file's title, or None if it has none.
        source: the file under test, or None in logs that don't record it.
        labels: the score's words for pass, fail and inconclusive.
        checks: each check the file names -> its CheckMinimum.
        min_pass_rate: the file's own minimum, or None.
    """

    eval_file: str
    title: str | None
    source: str | None
    labels: tuple[str, str, str]
    checks: Mapping[str, CheckMinimum]
    min_pass_rate: float | None

    @property
    def heading(self) -> str:
        """The name the report gives the file: its title, else its source, else its path."""
        return self.title or self.source or self.eval_file


@dataclass(frozen=True)
class Record:
    """One scored run of one scenario.

    role and file come from the log's metadata; both are None for a log
    written before it recorded them.
    """

    eval_name: str
    variant: str
    scenario: str
    outcome: str
    metadata: dict = field(default_factory=dict, compare=True, hash=False)
    role: str | None = None
    file: FileInfo | None = field(default=None, compare=True, hash=False)


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
    file: FileInfo | None = None  # None when every log was written before it was recorded
    roles: dict[str, str] = field(default_factory=dict)  # variant -> role, where the logs record it


def records_from_log(log: EvalLog) -> list[Record]:
    """One Record per sample in a log; a sample that errored gets outcome "error".

    The eval, variant and role come from the log's metadata when it names its
    eval file; an older log's come from its task name.
    """
    metadata = getattr(log.eval, "metadata", None) or {}  # a hand-built log in a test may have none
    info = _file_info(metadata)
    if info:
        eval_name, variant, role = metadata["eval_name"], metadata["variant"], metadata.get("variant_role")
    else:
        (eval_name, variant), role = _split_task_name(log.eval.task), None
    out = []
    for sample in log.samples or []:
        if sample.error or not sample.scores:
            outcome = "error"
        else:
            outcome = next(iter(sample.scores.values())).answer or "error"
        out.append(Record(eval_name, variant, str(sample.id), outcome, dict(sample.metadata or {}), role, info))
    return out


def _file_info(metadata: dict) -> FileInfo | None:
    """The eval file a log's metadata describes, or None for a log written before it did."""
    if "eval_file" not in metadata:
        return None
    return FileInfo(
        eval_file=metadata["eval_file"],
        title=metadata.get("title"),
        source=metadata.get("source"),
        labels=tuple(metadata["labels"]),
        checks={name: CheckMinimum(c["judges"], c["min_pass_rate"]) for name, c in metadata["checks"].items()},
        min_pass_rate=metadata.get("min_pass_rate"),
    )


def _split_task_name(task: str) -> tuple[str, str]:
    """'agents_md_template' -> ('agents_md', 'template')."""
    for name in EVALS:
        if task.startswith(f"{name}_"):
            return name, task.removeprefix(f"{name}_")
    raise ValueError(f"unknown eval task {task!r}; expected one of {list(EVALS)} plus a variant")


def summarise(records: list[Record]) -> dict[str, EvalSummary]:
    """Count outcomes per eval, scenario and variant."""
    rows: dict[str, dict[str, dict[str, Cell]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(Cell)))
    overall: dict[str, dict[str, Cell]] = defaultdict(lambda: defaultdict(Cell))
    gaps: dict[str, dict[str, str]] = defaultdict(dict)
    files: dict[str, FileInfo] = {}
    roles: dict[str, dict[str, str]] = defaultdict(dict)
    for r in records:
        if r.file:
            files.setdefault(r.eval_name, r.file)
        if r.role:
            roles[r.eval_name][r.variant] = r.role
    for r in records:
        passing, failing = _labels(r.eval_name, files.get(r.eval_name))[:2]
        if r.metadata.get("known_gap"):
            gaps[r.eval_name][r.scenario] = r.metadata["known_gap"]
        targets = [rows[r.eval_name][r.scenario][r.variant]]
        if not r.metadata.get("known_gap"):
            targets.append(overall[r.eval_name][r.variant])
        for cell in targets:
            if r.outcome == passing:
                cell.passed += 1
            elif r.outcome == failing:
                cell.failed += 1
            else:
                cell.inconclusive += 1
    return {
        name: EvalSummary(
            rows={s: dict(v) for s, v in rows[name].items()},
            overall=dict(overall[name]),
            known_gaps=gaps[name],
            file=files.get(name),
            roles=roles[name],
        )
        for name in rows
    }


def _labels(eval_name: str, info: FileInfo | None) -> tuple[str, str, str]:
    """The eval's words for pass, fail and inconclusive: its log's, or EVALS' for an older log."""
    return info.labels if info else EVALS[eval_name][:3]


def file_minimum(
    info: FileInfo,
    min_adherence: float | None = None,
    min_held: float | None = None,
    min_rates: Mapping[str, float] | None = None,
) -> float:
    """The minimum pass rate an eval file's template must reach.

    The highest of its checks' minimums, or the file's own min_pass_rate if
    that is higher: a file can raise its bar, never lower it. A check's
    minimum is `min_rates[check]` if given (--min-rate), else `min_adherence`
    for an agent-judged check or `min_held` for a harness-judged one if
    given, else the default the check declares.
    """
    by_judges = {"agent": min_adherence, "harness": min_held}
    minimums = []
    for name, check in info.checks.items():
        replaced = by_judges[check.judges]
        minimums.append((min_rates or {}).get(name, check.min_pass_rate if replaced is None else replaced))
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

    Two bars per eval file: the overall rate must reach its minimum, and at
    least `min_conclusive` of the template's runs must be conclusive. The
    rate leaves inconclusive runs out, so without the second bar one followed
    run among many that ended early (an outage, a spent credit balance) would
    pass at 100%. Errored and not-attempted runs count as not conclusive.
    Only the template counts: baselines and controls never affect the gate.

    A file's minimum is file_minimum's, from the checks its logs record.
    Logs written before they recorded them are gated as they always were:
    agents_md at `min_adherence` and kilo_jsonc at `min_held`, MIN_ADHERENCE
    and MIN_HELD when not given; `min_rates` doesn't apply to them.
    """
    legacy = {"agents_md": MIN_ADHERENCE if min_adherence is None else min_adherence,
              "kilo_jsonc": MIN_HELD if min_held is None else min_held}
    problems = []
    for name, eval_summary in summary.items():
        if eval_summary.file:
            minimum = file_minimum(eval_summary.file, min_adherence, min_held, min_rates)
        else:
            minimum = legacy[name]
        cell = eval_summary.overall.get(TEMPLATE_VARIANT, Cell())
        rate = cell.rate
        if rate is None or rate < minimum:
            shown = "no conclusive runs" if rate is None else f"{rate:.0%}"
            problems.append(f"{name}: template {shown} is below the minimum {minimum:.0%}")
        conclusive = cell.passed + cell.failed
        runs = conclusive + cell.inconclusive
        if conclusive and conclusive / runs < min_conclusive:  # no conclusive runs at all is reported above
            problems.append(
                f"{name}: only {conclusive}/{runs} template runs were conclusive, below the minimum {min_conclusive:.0%}"
            )
    return problems


def render_markdown(summary: dict[str, EvalSummary]) -> str:
    """Results as Markdown tables, one per eval file.

    Each is headed with the file's title and its rate in its own words. Its
    variants are in role order (baselines, template, controls; by name within
    a role), with one Δ column per variant other than the template. A file
    run in the template only says why it has no Δ, and a control variant
    that never failed is named below the table. An older log's eval shows the
    two variants it always ran, as it always did, without those notes.
    """
    parts = []
    for name, s in summary.items():
        passing, failing, neither = _labels(name, s.file)
        neither = neither.replace("_", " ")
        title = s.file.heading if s.file else LEGACY_TITLES[name]
        variants = _file_variants(s) if s.file else EVALS[name][3]
        others = [v for v in variants if v != TEMPLATE_VARIANT]
        deltas = ["Δ"] if len(others) == 1 else [f"Δ {v}" for v in others]
        parts.append(f"## {title}: {passing} / ({passing} + {failing})\n")
        parts.append("| Scenario | " + " | ".join([*variants, *deltas]) + " |")
        parts.append("|---" * (1 + len(variants) + len(deltas)) + "|")
        for scenario, cells in sorted(s.rows.items()):
            label = f"{scenario} (known gap)" if scenario in s.known_gaps else scenario
            parts.append(_row(label, cells, variants, others, neither))
        parts.append(_row("**overall**", s.overall, variants, others, neither))
        if s.known_gaps:
            parts.append("\nKnown gaps, excluded from overall and thresholds:")
            parts.extend(f"- {scenario}: {note}" for scenario, note in sorted(s.known_gaps.items()))
        if not others:
            parts.append(f"\nOnly `{TEMPLATE_VARIANT}` ran for this file, so there is no Δ.")
        parts.extend(f"\n{warning}" for warning in _control_warnings(s, variants))
        parts.append("")
    return "\n".join(parts)


def _file_variants(s: EvalSummary) -> tuple[str, ...]:
    """The variants an eval file's logs ran, in role order and by name within a role."""
    seen = {variant for cells in s.rows.values() for variant in cells}
    rank = {role: index for index, role in enumerate(ROLE_ORDER)}
    return tuple(sorted(seen, key=lambda v: (rank.get(s.roles.get(v), len(ROLE_ORDER)), v)))


def _control_warnings(s: EvalSummary, variants: tuple[str, ...]) -> list[str]:
    """One line per control variant with runs outside the known gaps and no failing run among them.

    Such a control didn't show its checks can fail, so a passing template
    may not show the rule holding. It doesn't fail the gate: with a few runs
    a control can pass by chance, and a gate on it would make CI flaky.
    """
    warnings = []
    for variant in variants:
        cell = s.overall.get(variant)
        if s.roles.get(variant) != "control" or cell is None or cell.failed:
            continue
        runs = cell.passed + cell.inconclusive
        plural = "run" if runs == 1 else "runs"
        warnings.append(
            f"`{variant}` never failed in {runs} {plural}, so the template's result may not show the rule holding."
        )
    return warnings


def render_header(
    logs: list[EvalLog], evals_path: str | Path | None = None, min_rates: Mapping[str, float] | None = None
) -> str:
    """The report's lines after its target and model: what an auditor needs to tie it to the scenarios.

    For logs that name their eval file: the Whitney and harness versions they
    recorded (every value, if they differ; "not recorded" if none did) and
    `evals_path`, then each eval file with its title and the SHA-256 of the
    file as it is now under `evals_path`. Then each minimum `min_rates`
    replaced for this run. Empty when there is none of these, so logs
    written before them render as before.
    """
    files: dict[str, FileInfo] = {}
    recorded: dict[str, set[str]] = {key: set() for key in ("whitney_version", "harness_version")}
    for log in logs:
        metadata = log.eval.metadata or {}
        info = _file_info(metadata)
        if info is None:
            continue
        files.setdefault(info.eval_file, info)
        for key, values in recorded.items():
            if metadata.get(key):
                values.add(str(metadata[key]))
    lines = []
    if files:
        shown = {key: ", ".join(f"`{v}`" for v in sorted(values)) or NOT_RECORDED for key, values in recorded.items()}
        evals = f"`{evals_path}`" if evals_path else NOT_GIVEN
        lines += [
            f"Whitney: {shown['whitney_version']} · Harness: {shown['harness_version']} · Evals: {evals}",
            "",
            "| Eval file | Title | SHA-256 |",
            "|---|---|---|",
        ]
        lines += [f"| `{path}` | {info.heading} | {_sha256(evals_path, path)} |" for path, info in sorted(files.items())]
        lines.append("")
    if min_rates:
        replaced = ", ".join(f"`{name}={value:g}`" for name, value in sorted(min_rates.items()))
        lines += [f"Minimum pass rates replaced for this run (--min-rate): {replaced}.", ""]
    return "\n".join(lines) + "\n" if lines else ""


def _sha256(evals_path: str | Path | None, eval_file: str) -> str:
    """The eval file's SHA-256 as it is now, for the header; says so if it can't be read."""
    if not evals_path:
        return NOT_GIVEN
    path = Path(evals_path) / eval_file
    if not path.is_file():
        return "not found"
    return f"`{hashlib.sha256(path.read_bytes()).hexdigest()}`"


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
    permission_overlays: dict[str, set[str]] = defaultdict(set)
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
            permission_overlays[overlay].add(task)
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
        for overlay, users in sorted(permission_overlays.items(), key=lambda item: sorted(item[1])):
            sections.extend([f"Used by: {', '.join(f'`{task}`' for task in sorted(users))}.", "", "```json", overlay, "```", ""])

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
