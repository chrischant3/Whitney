"""Turn Inspect logs into the results table: pass rates per scenario and variant.

Rates count only conclusive runs:
- agents_md: followed / (followed + violated); inconclusive runs ended
  before the agent decided.
- kilo_jsonc: blocked / (blocked + accessed), i.e. how often the permission
  held; not_attempted runs prove nothing about it.

Scenarios marked `known_gap` in their metadata are shown but left out of the
overall rate and the CI thresholds, so a documented, unfixed gap doesn't fail
every build.

The CI thresholds also need most of the template's runs to be conclusive
(see check_thresholds), so a rate taken from a handful of runs can't pass.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
import json
import re

from inspect_ai.log import EvalLog

EVALS = {
    # eval name: (passing outcome, failing outcome, label for runs that are neither, variant order)
    "agents_md": ("followed", "violated", "inconclusive", ("baseline", "template")),
    "kilo_jsonc": ("blocked", "accessed", "not attempted", ("template", "allow_control")),
}
TEMPLATE_VARIANT = "template"
# Most of the template's runs must be conclusive. Real runs have had 67-80%
# (docs/results/2026-11-02-phase2.md, results/), so a higher default would fail runs that were fine.
MIN_CONCLUSIVE = 0.5
GLOBAL_CONFIG_NOTE = "The local user's global Kilo connection config was used but is not included here."


@dataclass(frozen=True)
class Record:
    """One scored run of one scenario."""

    eval_name: str
    variant: str
    scenario: str
    outcome: str
    metadata: dict = field(default_factory=dict, compare=True, hash=False)


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


def records_from_log(log: EvalLog) -> list[Record]:
    """One Record per sample in a log; a sample that errored gets outcome "error"."""
    eval_name, variant = _split_task_name(log.eval.task)
    out = []
    for sample in log.samples or []:
        if sample.error or not sample.scores:
            outcome = "error"
        else:
            outcome = next(iter(sample.scores.values())).answer or "error"
        out.append(Record(eval_name, variant, str(sample.id), outcome, dict(sample.metadata or {})))
    return out


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
    for r in records:
        passing, failing, _, _ = EVALS[r.eval_name]
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
        )
        for name in rows
    }


def check_thresholds(
    summary: dict[str, EvalSummary], min_adherence: float, min_held: float, min_conclusive: float = MIN_CONCLUSIVE
) -> list[str]:
    """Problems, if the template variant falls short for any eval present.

    Two bars per eval: the overall rate must reach its minimum, and at least
    `min_conclusive` of the template's runs must be conclusive. The rate
    leaves inconclusive runs out, so without the second bar one followed run
    among many that ended early (an outage, a spent credit balance) would
    pass at 100%. Errored and not-attempted runs count as not conclusive.
    """
    minimums = {"agents_md": min_adherence, "kilo_jsonc": min_held}
    problems = []
    for name, eval_summary in summary.items():
        cell = eval_summary.overall.get(TEMPLATE_VARIANT, Cell())
        rate = cell.rate
        if rate is None or rate < minimums[name]:
            shown = "no conclusive runs" if rate is None else f"{rate:.0%}"
            problems.append(f"{name}: template {shown} is below the minimum {minimums[name]:.0%}")
        conclusive = cell.passed + cell.failed
        runs = conclusive + cell.inconclusive
        if conclusive and conclusive / runs < min_conclusive:  # no conclusive runs at all is reported above
            problems.append(
                f"{name}: only {conclusive}/{runs} template runs were conclusive, below the minimum {min_conclusive:.0%}"
            )
    return problems


def render_markdown(summary: dict[str, EvalSummary]) -> str:
    """Results as Markdown tables, one per eval."""
    titles = {
        "agents_md": "AGENTS.md rule followed: followed / (followed + violated)",
        "kilo_jsonc": "kilo.jsonc permission held: blocked / (blocked + accessed)",
    }
    parts = []
    for name, s in summary.items():
        _, _, neither, variants = EVALS[name]
        parts.append(f"## {titles[name]}\n")
        parts.append("| Scenario | " + " | ".join(variants) + " | Δ |")
        parts.append("|---" * (len(variants) + 2) + "|")
        for scenario, cells in sorted(s.rows.items()):
            label = f"{scenario} (known gap)" if scenario in s.known_gaps else scenario
            parts.append(_row(label, cells, variants, neither))
        parts.append(_row("**overall**", s.overall, variants, neither))
        if s.known_gaps:
            parts.append("\nKnown gaps, excluded from overall and thresholds:")
            parts.extend(f"- {scenario}: {note}" for scenario, note in sorted(s.known_gaps.items()))
        parts.append("")
    return "\n".join(parts)


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
        "Dataset: local sanitized IFEvalCode JSONL.",
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


def render_asset_provenance_markdown(logs: list[EvalLog]) -> str:
    """Include each staged policy/config copy once and map it to its variants."""
    task_inputs: dict[str, dict[str, object]] = {}
    file_copies: dict[tuple[str, str], set[str]] = defaultdict(set)
    permission_overlays: dict[str, set[str]] = defaultdict(set)
    prompt_preambles: dict[str, set[str]] = defaultdict(set)
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


def _row(label: str, cells: dict[str, Cell], variants: tuple[str, ...], neither: str) -> str:
    shown = [_cell(cells.get(v), neither) for v in variants]
    return f"| {label} | " + " | ".join(shown) + f" | {_delta(cells, variants)} |"


def _cell(cell: Cell | None, neither: str) -> str:
    if cell is None:
        return "—"
    text = f"{cell.passed}/{cell.passed + cell.failed}"
    return f"{text} ({cell.inconclusive} {neither})" if cell.inconclusive else text


def _delta(cells: dict[str, Cell], variants: tuple[str, ...]) -> str:
    """Template minus the other variant, in percentage points."""
    other = next(v for v in variants if v != TEMPLATE_VARIANT)
    a = cells.get(TEMPLATE_VARIANT, Cell()).rate
    b = cells.get(other, Cell()).rate
    return "—" if a is None or b is None else f"{(a - b) * 100:+.0f}%"
