"""Deprecated: use `whitney run`. Run the evals (both variants each), print the results table, and fail if the template is below the bar.

    python scripts/run_evals.py --target kilo --model kilo/anthropic/claude-haiku-4.5 --epochs 3     # Kilo, Kilo Gateway
    python scripts/run_evals.py --target claude-code --model anthropic/claude-haiku-4-5 --epochs 3   # Claude Code, Anthropic API
    python scripts/run_evals.py --target kilo --model anthropic/claude-haiku-4-5 --api-key-env ANTHROPIC_API_KEY  # Kilo, own key
    python scripts/run_evals.py --target kilo --execution docker --model kilo/anthropic/claude-haiku-4.5  # Kilo in Docker
    python scripts/run_evals.py --report-only logs/phase2b      # re-render existing logs, no model calls; as `whitney report logs/phase2b`

There is no default target or model: --target, the harness under test, and
--model, the model under evaluation, are both required.

Exit code 1 if, for any eval file, the template variant's overall rate is
below the file's minimum, or fewer than --min-conclusive of its runs were
conclusive, so CI can gate on it. A file's minimum is the highest of its
checks' minimums, or its own min_pass_rate if higher. --min-rate CHECK=VALUE
replaces one check's minimum for this run. --min-adherence and --min-held
are deprecated aliases for --min-rate no_secret_committed=VALUE and
outside_canary_not_reached=VALUE, the checks AGENTS.md and kilo.jsonc are
scored by, when given; --min-rate wins over
them. The report prints each minimum replaced. Writes the table to
results/<target>_<model>_<timestamp>.md.

Exit code 2 if a task can't be built, such as for an invalid eval file:
every task is built before any runs, so nothing calls a model first.

Kept as a wrapper over whitney.cli, with today's defaults (Whitney's own
template and evals/, logs/ and results/ in the repo), task names, labels and
tables, until a planned breaking release. It doesn't check the harness's
version first, as `whitney run` does, so its reports say "not recorded".
"""

import argparse
import sys
from pathlib import Path

from inspect_ai import eval
from inspect_ai.log import list_eval_logs, read_eval_log

from whitney import cli
from whitney.interference import ifevalcode
from whitney.report import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD, replaced_minimums
from whitney.targets import TARGETS
from whitney.tasks import EVALS_DIR, agents_md, kilo_jsonc

ROOT = Path(__file__).parent.parent
DEPRECATED = (
    "run_evals.py is deprecated: use `whitney run` with --project-path and --evals-path (see the README); "
    "it stays until a planned breaking release"
)
# Shared with `whitney run`, under the names this script has always had.
_rate = cli.rate
_min_rates = cli.min_rates
_filename_model = cli.filename_model
_result_filename = cli.result_filename


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
    print(DEPRECATED, file=sys.stderr)
    if args.report_only:
        logs = [read_eval_log(info.name) for info in list_eval_logs(args.report_only)]
    else:
        try:
            runs = _tasks(args)
        except ValueError as e:  # an invalid eval file, or an option a task refuses
            print(f"run_evals.py: error: {e}", file=sys.stderr)
            return 2
        logs = []
        for name, task in runs:
            logs += eval(
                task,
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

    rates = replaced_minimums(args.min_adherence, args.min_held, args.min_rate)
    # EVALS_DIR: the eval files agents_md and kilo_jsonc read, so the header's SHA-256s are theirs.
    return cli.write_report(
        logs, target=_report_target_name(logs, args), model=_report_model_name(logs, args), evals_path=EVALS_DIR,
        min_rates=rates, min_conclusive=args.min_conclusive, results_dir=args.results_dir,
    )


def _tasks(args: argparse.Namespace) -> list[tuple[str, object]]:
    """(eval name, task) for every run --evals asks for, all built before any runs.

    Raises:
        ValueError: a task can't be built (EvalFileError for an invalid eval file).
    """
    runs = []
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
            runs.append((name, task_fn(**task_args)))
    return runs


def _report_model_name(logs, args: argparse.Namespace) -> str:
    """The model the report names: --model, or for --report-only the one the logs record (cli.report_model)."""
    if not args.report_only:
        return args.model
    return cli.report_model(logs, args.model)


def _report_target_name(logs, args: argparse.Namespace) -> str:
    """The target the report covers: --target, or for --report-only the one the logs record (cli.report_target)."""
    if not args.report_only:
        return args.target
    return cli.report_target(logs, args.target)


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
    # No default: left out, they replace nothing, so the check's own MIN_ADHERENCE or MIN_HELD applies.
    p.add_argument(
        "--min-adherence", type=_rate,
        help=f"deprecated: --min-rate no_secret_committed=VALUE (left out: the check's own, MIN_ADHERENCE={MIN_ADHERENCE})",
    )
    p.add_argument(
        "--min-held", type=_rate,
        help=f"deprecated: --min-rate outside_canary_not_reached=VALUE (left out: the check's own, MIN_HELD={MIN_HELD})",
    )
    p.add_argument(
        "--min-conclusive",
        type=_rate,
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
    # Inspect waits forever with max_samples 0, and refuses epochs below 1 only once the run has started.
    for option, value in (("--epochs", args.epochs), ("--max-samples", args.max_samples)):
        if value < 1:
            p.error(f"{option} must be positive")
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
