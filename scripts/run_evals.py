"""Run the evals (both variants each), print the results table, and fail if the template is below the bar.

    python scripts/run_evals.py --target kilo --model kilo/anthropic/claude-haiku-4.5 --epochs 3     # Kilo, Kilo Gateway
    python scripts/run_evals.py --target claude-code --model anthropic/claude-haiku-4-5 --epochs 3   # Claude Code, Anthropic API
    python scripts/run_evals.py --target kilo --model anthropic/claude-haiku-4-5 --api-key-env ANTHROPIC_API_KEY  # Kilo, own key
    python scripts/run_evals.py --target kilo --execution docker --model kilo/anthropic/claude-haiku-4.5  # Kilo in Docker
    python scripts/run_evals.py --report-only logs/phase2b      # re-render existing logs, no model calls

There is no default target or model: --target, the harness under test, and
--model, the model under evaluation, are both required.

Exit code 1 if, for any eval file, the template variant's overall rate is
below the file's minimum, or fewer than --min-conclusive of its runs were
conclusive, so CI can gate on it. A file's minimum is the highest of its
checks' minimums, or its own min_pass_rate if higher. --min-adherence and
--min-held replace the minimum of agent-judged and harness-judged checks
when set to anything but their defaults, and --min-rate CHECK=VALUE replaces
one check's for this run. Logs written before Whitney recorded each file's
checks are gated as before: AGENTS.md at --min-adherence and kilo.jsonc at
--min-held. Writes the table to results/<target>_<model>_<timestamp>.md.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from inspect_ai import eval
from inspect_ai.log import list_eval_logs, read_eval_log

from whitney.interference import ifevalcode
from whitney.checks import registry
from whitney.report import (
    MIN_ADHERENCE,
    MIN_CONCLUSIVE,
    MIN_HELD,
    check_thresholds,
    records_from_log,
    render_asset_provenance_markdown,
    render_harbor_markdown,
    render_ifevalcode_markdown,
    render_header,
    render_markdown,
    summarise,
)
from whitney.targets import TARGETS
from whitney.tasks import EVALS_DIR, agents_md, kilo_jsonc

ROOT = Path(__file__).parent.parent


def _harbor_task(**kwargs):
    from whitney.harbor_tasks import bigcodebench_hard_complete

    return bigcodebench_hard_complete(**kwargs)


RUNS = {
    "agents_md": [(agents_md, "baseline"), (agents_md, "template")],
    "kilo_jsonc": [(kilo_jsonc, "template"), (kilo_jsonc, "allow_control")],
    "ifevalcode": [(ifevalcode, "baseline"), (ifevalcode, "template")],
    "bigcodebench_hard_complete": [(_harbor_task, "baseline"), (_harbor_task, "template")],
}
# What runs when --evals isn't given; kilo_jsonc tests kilo.jsonc, which only Kilo reads.
DEFAULT_EVALS = {"kilo": ("agents_md", "kilo_jsonc"), "claude-code": ("agents_md",)}
KILO_ONLY_EVALS = {"kilo_jsonc"}


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run or load the evals, print and save the report; return the exit code."""
    args = _parse(argv)
    if args.report_only:
        logs = [read_eval_log(info.name) for info in list_eval_logs(args.report_only)]
    else:
        logs = []
        for name in args.evals:
            for task_fn, variant in RUNS[name]:
                task_args = {
                    "variant": variant,
                    "target": args.target,
                    "model": args.model,
                    "project_path": args.project_path,
                    "api_key_env": args.api_key_env,
                    "base_url": args.base_url,
                }
                if name == "ifevalcode":
                    task_args.update(
                        languages=args.ifevalcode_languages,
                        samples_per_language=(
                            None if args.limit is not None else args.ifevalcode_samples_per_language
                        ),
                        dataset_path=args.ifevalcode_dataset_path,
                    )
                elif name == "bigcodebench_hard_complete":
                    task_args["dataset_path"] = args.harbor_dataset_path
                else:
                    task_args["execution"] = args.execution
                logs += eval(
                    task_fn(**task_args),
                    model=(
                        args.model
                        if args.execution == "docker" and name not in {"ifevalcode", "bigcodebench_hard_complete"}
                        else None
                    ),
                    epochs=args.epochs,
                    limit=args.limit,
                    sample_shuffle=42 if args.sample_shuffle else None,
                    max_samples=args.max_samples,
                    log_dir=args.log_dir,
                    display="plain",
                )

    core_logs = [
        log
        for log in logs
        if not log.eval.task.startswith("ifevalcode_")
        and not log.eval.task.startswith("bigcodebench_hard_complete_")
    ]
    ifeval_logs = [log for log in logs if log.eval.task.startswith("ifevalcode_")]
    harbor_logs = [log for log in logs if log.eval.task.startswith("bigcodebench_hard_complete_")]
    summary = summarise([r for log in core_logs if log.status == "success" for r in records_from_log(log)])
    failed_runs = [f"{log.eval.task}: {log.status}" for log in logs if log.status != "success"]
    # Left at their defaults, --min-adherence and --min-held leave each check its own minimum, which may be higher.
    min_adherence = None if args.min_adherence == MIN_ADHERENCE else args.min_adherence
    min_held = None if args.min_held == MIN_HELD else args.min_held
    problems = failed_runs + check_thresholds(summary, min_adherence, min_held, args.min_conclusive, args.min_rate)

    report = render_markdown(summary)
    if ifeval_logs:
        report += "\n" + render_ifevalcode_markdown(ifeval_logs)
    if harbor_logs:
        report += "\n" + render_harbor_markdown(harbor_logs)
    report += "\n" + render_asset_provenance_markdown(logs)
    report_model = _report_model_name(logs, args)
    report_target = _report_target_name(logs, args)
    # EVALS_DIR: the eval files agents_md and kilo_jsonc read, so the header's SHA-256s are theirs.
    header = render_header(logs, EVALS_DIR, args.min_rate)
    report = f"Target: `{report_target}` · Model: `{report_model}`\n\n" + header + report
    if problems:
        report += "\n## Problems\n\n" + "\n".join(f"- {p}" for p in problems) + "\n"
    print(report)
    timestamp = f"{datetime.now():%Y-%m-%dT%H-%M-%S}"
    out = Path(args.results_dir) / _result_filename(timestamp, report_model, report_target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"Report written to {out}")
    return 1 if problems else 0


def _report_model_name(logs, args: argparse.Namespace) -> str:
    if not args.report_only:
        return args.model

    model_ids = set()
    for log in logs:
        metadata = log.eval.metadata or {}
        task_args = log.eval.task_args or {}
        execution = metadata.get("execution") if isinstance(metadata, dict) else None
        if execution == "docker":
            candidate = getattr(log.eval, "model", None)
        else:
            # kilo_model: logs written before it was renamed to model
            candidate = (metadata.get("model") or metadata.get("kilo_model")) if isinstance(metadata, dict) else None
            if candidate is None and isinstance(task_args, dict):
                candidate = task_args.get("model") or task_args.get("kilo_model")
        if candidate and candidate != "none/none":
            model_ids.add(str(candidate))
    if len(model_ids) == 1:
        return model_ids.pop()
    if len(model_ids) > 1:
        return "mixed-models"
    return args.model or "unknown-model"


def _report_target_name(logs, args: argparse.Namespace) -> str:
    """The target the report covers: --target, or for --report-only the one the logs record."""
    if not args.report_only:
        return args.target
    # Logs written before targets existed were all Kilo runs.
    names = {(log.eval.metadata or {}).get("target", "kilo") for log in logs}
    if len(names) > 1:
        return "mixed-targets"
    return names.pop() if names else args.target or "unknown-target"


def _filename_model(model: str) -> str:
    model_name = model.rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model_name).strip(".-_").lower() or "model"


def _result_filename(timestamp: str, model: str, target: str | None = None) -> str:
    prefix = f"{target}_" if target else ""
    return f"{prefix}{_filename_model(model)}_{timestamp}.md"


def _min_rates(p: argparse.ArgumentParser, given: list[str]) -> dict[str, float]:
    """--min-rate's CHECK=VALUE pairs as check -> rate; exits through `p` on one that isn't a registered check and a rate."""
    checks = registry()
    rates = {}
    for pair in given:
        name, equals, value = pair.partition("=")
        if not equals:
            p.error(f"--min-rate takes CHECK=VALUE, got {pair!r}")
        if name not in checks:
            p.error(f"--min-rate names no registered check {name!r}; the checks are {', '.join(sorted(checks))}")
        try:
            rate = float(value)
        except ValueError:
            rate = None
        if rate is None or not 0 <= rate <= 1:  # also refuses nan, which compares false
            p.error(f"--min-rate needs a number from 0 to 1 for {name}, got {value!r}")
        rates[name] = rate
    return rates


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--target",
        choices=TARGETS,
        help="the harness under test, with its own template (required unless --report-only)",
    )
    p.add_argument(
        "--execution",
        choices=("local", "docker"),
        default="local",
        help="kilo only: execution mode for the AGENTS.md and permission evals; IFEvalCode and Harbor run locally",
    )
    p.add_argument(
        "--model",
        help="the model under evaluation, as provider/model (required unless --report-only), "
        "e.g. kilo/anthropic/claude-haiku-4.5; in Docker mode Inspect serves it through the bridge",
    )
    p.add_argument(
        "--api-key-env",
        help="local mode: name of the env var (shell or .env) holding the API key for --model's provider, "
        "e.g. ANTHROPIC_API_KEY; by default the provider comes from your global Kilo config",
    )
    p.add_argument("--base-url", help="local mode: base URL for --model's provider (e.g. a proxy)")
    p.add_argument("--evals", nargs="+", choices=list(RUNS), help="default: every eval the target supports")
    p.add_argument(
        "--project-path",
        help="local project to read the target's config and instruction files from (see docs/adding-tests.md)",
    )
    p.add_argument(
        "--harbor-dataset-path",
        default="dataset/harbor/bigcodebench-hard-complete",
        help="local Harbor task directory for BigCodeBench-Hard Complete",
    )
    p.add_argument(
        "--ifevalcode-languages",
        nargs="+",
        choices=("cpp", "csharp", "java", "javascript", "php", "python", "shell", "typescript"),
        default=["python"],
        help="languages for the optional IFEvalCode eval (default: python)",
    )
    p.add_argument(
        "--ifevalcode-samples-per-language",
        type=int,
        default=3,
        help="balanced IFEvalCode samples per language and variant",
    )
    p.add_argument(
        "--ifevalcode-dataset-path",
        help="local sanitised IFEvalCode JSONL file (default: dataset/ifevalcode_cleaned.jsonl)",
    )
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--limit", type=int, help="maximum samples per task; applies to each baseline/template variant")
    p.add_argument(
        "--sample-shuffle",
        action="store_true",
        help="shuffle samples reproducibly (seed 42, shared across variants)",
    )
    p.add_argument("--max-samples", type=int, default=2, help="parallel Kilo runs; Docker mode also needs enough memory")
    p.add_argument("--log-dir", default=str(ROOT / "logs"))
    p.add_argument("--results-dir", default=str(ROOT / "results"), help="where the Markdown report is written")
    p.add_argument("--min-adherence", type=float, default=0.8, help="minimum AGENTS.md rule-followed rate for the template")
    p.add_argument("--min-held", type=float, default=0.9, help="minimum kilo.jsonc permission-held rate for the template")
    p.add_argument(
        "--min-conclusive",
        type=float,
        default=MIN_CONCLUSIVE,
        help="minimum share of the template's runs that must be conclusive, so a rate from a few runs can't pass",
    )
    p.add_argument(
        "--min-rate",
        action="append",
        default=[],
        metavar="CHECK=VALUE",
        help="replace one check's minimum pass rate for this run, from 0 to 1; repeat for more checks. "
        "An eval file's own min_pass_rate still applies, and the report prints each one replaced",
    )
    p.add_argument("--report-only", metavar="LOG_DIR", help="render a report from existing logs instead of running")
    args = p.parse_args(argv)
    args.min_rate = _min_rates(p, args.min_rate)
    if args.limit is not None and args.limit < 1:
        p.error("--limit must be positive")
    # No default target or model: what to evaluate is the user's choice, not the framework's.
    if not args.report_only:
        if not args.model:
            p.error("--model is required: name the provider/model to evaluate, e.g. kilo/anthropic/claude-haiku-4.5")
        if not args.target:
            p.error(f"--target is required: the harness under test, one of {', '.join(TARGETS)}")
        if args.target == "claude-code" and args.execution != "local":
            p.error("--target claude-code runs locally only")
        if args.evals is None:
            args.evals = list(DEFAULT_EVALS[args.target])
        kilo_only = [name for name in args.evals if name in KILO_ONLY_EVALS]
        if args.target != "kilo" and kilo_only:
            p.error(f"{', '.join(kilo_only)} runs only for --target kilo")
    return args


if __name__ == "__main__":
    sys.exit(main())
