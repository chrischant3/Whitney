"""The `whitney` command: run a project's evals against a harness and model, or check them without a model.

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \\
        --project-path . --evals-path evals --epochs 3
    whitney validate --project-path . --evals-path evals [--target kilo] [--constraints CONSTRAINTS.md]

Exit codes, for every subcommand (docs/specs/package-and-expected-results.md):

| Code | `whitney run` | `whitney validate` |
|---|---|---|
| 0 | validation passed and every gate met (warnings allowed) | valid (warnings allowed) |
| 1 | a gate failed, or an Inspect log isn't `success` | a validation problem was found |
| 2 | couldn't run: bad arguments, a missing path, an invalid eval file, an unset credential variable, or the harness CLI not found | couldn't run: bad arguments or a missing path |

`whitney run` validates everything, checks the credential variable it was
named and runs `kilo --version` (or `claude --version`) before any sample,
so each of those fails with a 2 and a message rather than a run of errored
samples. There is no default for the two paths, the target or the model: a
consumer who forgot one would otherwise get a green run that tested
something else. Whitney reads no CI-specific environment variable, so the
same invocation works on any CI system.

scripts/run_evals.py, the deprecated runner, shares this module's report
and gate (write_report) and its option parsing (rate, min_rates).
"""

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from inspect_ai import eval as inspect_eval

# Inspect loads a .env file from the current folder (or above) when an eval
# starts; loading it the same way first lets the credential check below see
# what the run will see. Inspect has no public name for this.
from inspect_ai._util.dotenv import init_dotenv

from whitney import targets
from whitney.checks import CheckSpec, registry
from whitney.evalfile import EvalFile, Problem
from whitney.report import (
    ROLE_ORDER,
    check_thresholds,
    records_from_log,
    render_asset_provenance_markdown,
    render_harbor_markdown,
    render_header,
    render_ifevalcode_markdown,
    render_markdown,
    replaced_minimums,
    summarise,
)
from whitney.target_names import TARGETS
from whitney.tasks import scenario_eval
from whitney.thresholds import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD
from whitney.validate import ValidationResult, validate_evals, validate_project
from whitney.variants import TEMPLATE, declared_variants

# The CLI each target runs, checked with `<name> --version` before a run.
HARNESS_COMMANDS = {"kilo": "kilo", "claude-code": "claude"}
VERSION_TIMEOUT = 30  # seconds; `--version` answers at once, so a longer wait means a hung CLI
SAMPLE_SHUFFLE_SEED = 42  # as run_evals.py: the same order across variants and runs

# Help texts, kept out of the parser's code so each option reads as one line there.
HELP = {
    "run": "run the eval files against a harness and model, print and save the report, and gate on it",
    "validate": "check the eval files and the project without calling a model",
    "--target": "the harness under test",
    "--model": "the model under evaluation, as provider/model, e.g. kilo/anthropic/claude-haiku-4.5",
    "--project-path": "the project whose config and instruction files are under test; it is read, never written",
    "--evals-path": "the evals root: the eval files, plus their fixtures in fixtures/",
    "--only": "run only these eval files: paths relative to the evals root, or eval names",
    "--constraints": "a CONSTRAINTS.md-style file; each known_gap's ID must be a row in its Exceptions table",
    "--execution": "kilo only: local, or docker through Inspect's model bridge",
    "--api-key-env": "local mode: the variable (shell or .env) holding the API key for --model's provider; "
    "kilo without it uses your global Kilo config, claude-code defaults to ANTHROPIC_API_KEY",
    "--base-url": "kilo local mode: base URL for --model's provider (e.g. a proxy)",
    "--epochs": "runs of each scenario",
    "--limit": "at most this many scenarios per eval file and variant",
    "--sample-shuffle": f"shuffle the scenarios reproducibly (seed {SAMPLE_SHUFFLE_SEED}, the same for every variant)",
    "--max-samples": "harness runs at once; Docker mode also needs enough memory",
    "--log-dir": "where Inspect's logs go",
    "--results-dir": "where the Markdown report goes",
    "--min-rate": "replace one check's minimum pass rate for this run, from 0 to 1; repeat for more checks. "
    "An eval file's own min_pass_rate still applies, and the report prints each one replaced",
    "--min-conclusive": "minimum share of the template's runs that must be conclusive, so a rate from a few runs "
    "can't pass (default: MIN_CONCLUSIVE in whitney.thresholds)",
    "--min-adherence": f"deprecated: --min-rate no_secret_committed=VALUE (left out: the check's own, MIN_ADHERENCE={MIN_ADHERENCE})",
    "--min-held": f"deprecated: --min-rate outside_canary_not_reached=VALUE (left out: the check's own, MIN_HELD={MIN_HELD})",
}


class CannotRun(Exception):
    """Whitney couldn't run, so the command exits 2; the message says why and, where it can, what to do."""


def main(argv: list[str] | None = None) -> int:
    """Parse `argv` (default: the command line), run the subcommand and return its exit code."""
    try:
        args = parser().parse_args(argv)
        _check_args(args)
        return args.handler(args)
    except SystemExit as e:  # argparse: --help (0) or a usage error, already printed (2)
        return e.code
    except CannotRun as e:
        print(f"whitney: error: {e}", file=sys.stderr)
        return 2


def parser() -> argparse.ArgumentParser:
    """The `whitney` command's argument parser, with the `run` and `validate` subcommands."""
    p = argparse.ArgumentParser(prog="whitney", description=__doc__.split("\n")[0])
    commands = p.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help=HELP["run"], description=HELP["run"])
    run.add_argument("--target", choices=TARGETS, required=True, help=HELP["--target"])
    _add_paths(run)
    run.add_argument("--model", required=True, help=HELP["--model"])
    run.add_argument("--only", nargs="+", metavar="FILE_OR_NAME", help=HELP["--only"])
    run.add_argument("--execution", choices=("local", "docker"), default="local", help=HELP["--execution"])
    run.add_argument("--api-key-env", help=HELP["--api-key-env"])
    run.add_argument("--base-url", help=HELP["--base-url"])
    run.add_argument("--epochs", type=int, default=3, help=HELP["--epochs"])
    run.add_argument("--limit", type=int, help=HELP["--limit"])
    run.add_argument("--sample-shuffle", action="store_true", help=HELP["--sample-shuffle"])
    run.add_argument("--max-samples", type=int, default=2, help=HELP["--max-samples"])
    run.add_argument("--log-dir", default="logs", help=HELP["--log-dir"])
    run.add_argument("--results-dir", default="results", help=HELP["--results-dir"])
    run.add_argument("--min-rate", action="append", default=[], metavar="CHECK=VALUE", help=HELP["--min-rate"])
    run.add_argument("--min-conclusive", type=rate, default=MIN_CONCLUSIVE, help=HELP["--min-conclusive"])
    run.add_argument("--min-adherence", type=rate, help=HELP["--min-adherence"])
    run.add_argument("--min-held", type=rate, help=HELP["--min-held"])
    run.set_defaults(handler=_run, parser=run)
    validate = commands.add_parser("validate", help=HELP["validate"], description=HELP["validate"])
    validate.add_argument("--target", choices=TARGETS, help=HELP["--target"] + "; left out, every target a file applies to")
    _add_paths(validate)
    validate.set_defaults(handler=_validate)
    return p


def _add_paths(p: argparse.ArgumentParser) -> None:
    """The options both subcommands take besides --target: the two paths and the constraints file."""
    p.add_argument("--project-path", required=True, help=HELP["--project-path"])
    p.add_argument("--evals-path", required=True, help=HELP["--evals-path"])
    p.add_argument("--constraints", help=HELP["--constraints"])


def _check_args(args: argparse.Namespace) -> None:
    """What argparse can't check alone: --min-rate's pairs and --limit; then that the paths exist.

    Raises:
        SystemExit: through the subcommand's parser, for a bad option value.
        CannotRun: a path that isn't a folder, or a constraints file that isn't a file.
    """
    if args.command == "run":
        args.min_rate = min_rates(args.parser, args.min_rate)
        if args.limit is not None and args.limit < 1:
            args.parser.error("--limit must be positive")
    for option, folder in (("--project-path", args.project_path), ("--evals-path", args.evals_path)):
        if not Path(folder).is_dir():
            raise CannotRun(f"{option} {folder} isn't a folder")
    if args.constraints is not None and not Path(args.constraints).is_file():
        raise CannotRun(f"--constraints {args.constraints} isn't a file")


def _validate(args: argparse.Namespace) -> int:
    """`whitney validate`: print every problem and warning, then a count; 1 if there is a problem."""
    evals = Path(args.evals_path)
    result = validate_evals(evals, constraints=_constraints(args))
    checks = registry()
    for target in (args.target,) if args.target else TARGETS:
        applicable = [f for f in result.files if why_not_run(f, target, checks) is None]
        if applicable:
            result.problems += validate_project(applicable, Path(args.project_path), target, evals_path=evals)
    _print_findings(result)
    print(f"{evals}: {len(result.files)} eval file(s), {len(result.problems)} problem(s), {len(result.warnings)} warning(s)")
    return 1 if result.problems else 0


def _run(args: argparse.Namespace) -> int:
    """`whitney run`: validate, check the credentials and the harness, build every task, then run them and gate.

    Raises:
        CannotRun: anything that stops the run before its first sample.
    """
    _check_target(args)
    evals, project = Path(args.evals_path), Path(args.project_path)
    result = validate_evals(evals, constraints=_constraints(args))
    _print_findings(result, sys.stderr)
    if result.problems:
        raise CannotRun("the evals aren't valid; `whitney validate` lists the same problems")
    selected, not_run = _select(result.files, evals, args.target, args.only)
    project_problems = validate_project(selected, project, args.target, evals_path=evals)
    _print_findings(ValidationResult(problems=project_problems), sys.stderr)
    if project_problems:
        raise CannotRun("the evals don't fit the project; `whitney validate` lists the same problems")
    init_dotenv()
    missing = missing_credential(args.target, args.execution, args.api_key_env)
    if missing:
        raise CannotRun(missing)
    # In Docker mode the harness is the image's, not the host's, so the host has nothing to check.
    version = harness_version(args.target) if args.execution == "local" else None
    tasks = [
        _task(eval_file.path.relative_to(evals).as_posix(), variant, args, version)
        for eval_file in selected
        for variant in variant_order(eval_file)
    ]
    logs = []
    for task in tasks:
        logs += inspect_eval(
            task,
            model=args.model if args.execution == "docker" else None,
            epochs=args.epochs,
            limit=args.limit,
            sample_shuffle=SAMPLE_SHUFFLE_SEED if args.sample_shuffle else None,
            max_samples=args.max_samples,
            log_dir=args.log_dir,
            display="plain",
        )
    return write_report(
        logs, target=args.target, model=args.model, evals_path=args.evals_path,
        min_rates=replaced_minimums(args.min_adherence, args.min_held, args.min_rate),
        min_conclusive=args.min_conclusive, results_dir=args.results_dir, not_run=not_run,
    )


def _check_target(args: argparse.Namespace) -> None:
    """Raise CannotRun, with targets.check_target's message, if the target can't run in the run's execution mode.

    A base URL the target can't use is refused when its tasks are built (_task).
    """
    try:
        targets.check_target(args.target, args.execution)
    except ValueError as e:
        raise CannotRun(str(e)) from e


def _task(eval_file: str, variant: str, args: argparse.Namespace, version: str | None):
    """scenario_eval's task for one eval file and variant; a task it refuses to build stops the run.

    Raises:
        CannotRun: scenario_eval's ValueError, such as an option the harness can't use.
    """
    try:
        return scenario_eval(
            eval_file, variant, evals_path=args.evals_path, target=args.target, model=args.model,
            execution=args.execution, project_path=args.project_path, api_key_env=args.api_key_env,
            base_url=args.base_url, harness_version=version,
        )
    except ValueError as e:
        raise CannotRun(str(e)) from e


def _constraints(args: argparse.Namespace) -> Path | None:
    return Path(args.constraints) if args.constraints else None


def _print_findings(result: ValidationResult, stream=None) -> None:
    """Print each problem and warning, one a line, to `stream` (default: standard output)."""
    for problem in result.problems:
        print(f"error: {problem}", file=stream)
    for warning in result.warnings:
        print(f"warning: {warning}", file=stream)


def why_not_run(eval_file: EvalFile, target: str, checks: Mapping[str, CheckSpec]) -> str | None:
    """Why `eval_file` doesn't apply to `target`, or None if it does.

    A file applies if `target` is in its `targets` (left out: every target)
    and every check it names supports `target`. A check that isn't
    registered is left to validation.
    """
    if eval_file.targets is not None and target not in eval_file.targets:
        return f"it applies to {', '.join(eval_file.targets)}"
    named = {e.check for scenario in eval_file.resolved() for e in scenario.expect}
    unsupported = sorted(name for name in named if name in checks and target not in checks[name].targets)
    if unsupported:
        return f"its checks {', '.join(unsupported)} don't support it"
    return None


def _select(files: list[EvalFile], evals: Path, target: str, only: list[str] | None) -> tuple[list[EvalFile], dict[str, str]]:
    """The eval files to run, and each file left out because it doesn't apply to `target`, with why.

    Raises:
        CannotRun: an --only value names no eval file, or one that doesn't
            apply to the target; or no file applies at all.
    """
    checks = registry()
    reasons = {f.path.relative_to(evals).as_posix(): why_not_run(f, target, checks) for f in files}
    if only is None:
        chosen = files
    else:
        named = set()
        for wanted in only:
            matches = {f.path for f in files if wanted in (f.path.relative_to(evals).as_posix(), f.eval_name)}
            if not matches:
                raise CannotRun(f"--only {wanted} names no eval file under {evals}: give a path relative to it, or an eval name")
            named |= matches
        chosen = [f for f in files if f.path in named]  # in discovery order, whatever order --only gave
        for f in chosen:
            why = reasons[f.path.relative_to(evals).as_posix()]
            if why:
                raise CannotRun(f"{f.eval_name} doesn't apply to the {target} target: {why}")
    selected = [f for f in chosen if reasons[f.path.relative_to(evals).as_posix()] is None]
    if not selected:
        raise CannotRun(f"no eval file under {evals} applies to the {target} target")
    not_run = {path: why for path, why in reasons.items() if why}
    return selected, not_run


def variant_order(eval_file: EvalFile) -> list[str]:
    """`template` and the file's other variants in the report's order: baselines, template, controls; by name within each."""
    declared = declared_variants(eval_file)

    def rank(name: str) -> tuple[int, str]:
        return ROLE_ORDER.index(TEMPLATE if name == TEMPLATE else declared[name].role), name

    return sorted([TEMPLATE, *declared], key=rank)


def missing_credential(target: str, execution: str, api_key_env: str | None, environ: Mapping[str, str] = os.environ) -> str | None:
    """Why the run's credential variable isn't usable, or None.

    claude-code needs its key variable (api_key_env, default
    ANTHROPIC_API_KEY); a local kilo run needs api_key_env if it names one.
    A kilo run without one uses the global Kilo config, which Whitney can't
    check without calling the model. In Docker mode Kilo gets no key: the
    task refuses api_key_env there when it is built.
    """
    if target == "claude-code":
        name = api_key_env or "ANTHROPIC_API_KEY"
    elif execution == "local" and api_key_env:
        name = api_key_env
    else:
        return None
    return None if environ.get(name) else f"{name} is not set; put it in .env or the environment"


def harness_version(target: str) -> str:
    """The first line `<harness> --version` prints, such as "7.8.18" for Kilo.

    Raises:
        CannotRun: the CLI isn't on PATH, exits non-zero, prints nothing or
            doesn't finish within VERSION_TIMEOUT seconds.
    """
    command = HARNESS_COMMANDS[target]
    try:
        done = subprocess.run(
            [command, "--version"], capture_output=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=VERSION_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise CannotRun(f"`{command}` isn't on PATH; install the {target} target's CLI (see the README's Setup)") from e
    except subprocess.TimeoutExpired as e:
        raise CannotRun(f"`{command} --version` didn't finish in {VERSION_TIMEOUT}s") from e
    if done.returncode != 0:
        raise CannotRun(f"`{command} --version` exited {done.returncode}: {done.stderr.strip()[-300:]}")
    lines = done.stdout.strip().splitlines()
    if not lines:
        raise CannotRun(f"`{command} --version` printed no version")
    return lines[0].strip()


def write_report(
    logs: list,
    *,
    target: str,
    model: str,
    evals_path: str | Path | None,
    min_rates: Mapping[str, float],
    min_conclusive: float,
    results_dir: str | Path,
    not_run: Mapping[str, str] | None = None,
) -> int:
    """Gate the logs, then print the report and write it to results_dir; return 1 if the gate failed, else 0.

    The gate fails on an Inspect log that isn't `success`, and on any eval
    file whose template falls short (report.check_thresholds). The report is
    the header (target, model, versions, eval files), one table per eval
    file, the IFEvalCode and Harbor tables if those ran, the files not run
    for this target, the staged files' provenance and the gate's problems.

    Args:
        logs: Inspect logs, from this run or read back.
        target, model: what the report's first line names, and its file name.
        evals_path: the evals root the logs' eval files are relative to.
        min_rates: the check minimums replaced for this run.
        min_conclusive: the share of template runs that must be conclusive.
        results_dir: where the report is written, as <target>_<model>_<timestamp>.md.
        not_run: eval file -> why it doesn't apply to the target.
    """
    core_logs = [log for log in logs if not _benchmark(log.eval.task)]
    ifeval_logs = [log for log in logs if log.eval.task.startswith("ifevalcode_")]
    harbor_logs = [log for log in logs if log.eval.task.startswith("bigcodebench_hard_complete_")]
    summary = summarise([r for log in core_logs if log.status == "success" for r in records_from_log(log)])
    failed_runs = [f"{log.eval.task}: {log.status}" for log in logs if log.status != "success"]
    problems = failed_runs + check_thresholds(summary, min_conclusive=min_conclusive, min_rates=min_rates)

    report = render_markdown(summary)
    if ifeval_logs:
        report += "\n" + render_ifevalcode_markdown(ifeval_logs)
    if harbor_logs:
        report += "\n" + render_harbor_markdown(harbor_logs)
    if not_run:
        report += "\n## Not run for this target\n\n" + "".join(f"- `{path}`: {why}\n" for path, why in not_run.items())
    report += "\n" + render_asset_provenance_markdown(logs)
    header = render_header(logs, evals_path, min_rates)
    report = f"Target: `{target}` · Model: `{model}`\n\n" + header + report
    if problems:
        report += "\n## Problems\n\n" + "\n".join(f"- {p}" for p in problems) + "\n"
    print(report)
    timestamp = f"{datetime.now():%Y-%m-%dT%H-%M-%S}"
    out = Path(results_dir) / result_filename(timestamp, model, target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"Report written to {out}")
    return 1 if problems else 0


def _benchmark(task: str) -> bool:
    """True for an IFEvalCode or Harbor task, which the report shows in tables of their own."""
    return task.startswith(("ifevalcode_", "bigcodebench_hard_complete_"))


def filename_model(model: str) -> str:
    """The model's last segment as a file-name part: my-proxy/GPT 6 Luna -> gpt-6-luna."""
    model_name = model.rpartition("/")[2]
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model_name).strip(".-_").lower() or "model"


def result_filename(timestamp: str, model: str, target: str | None = None) -> str:
    """The report's file name: <target>_<model>_<timestamp>.md, without the target if there is none."""
    prefix = f"{target}_" if target else ""
    return f"{prefix}{filename_model(model)}_{timestamp}.md"


def min_rates(p: argparse.ArgumentParser, given: list[str]) -> dict[str, float]:
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
            rates[name] = rate(value)
        except argparse.ArgumentTypeError:
            p.error(f"--min-rate needs a number from 0 to 1 for {name}, got {value!r}")
    return rates


def rate(text: str) -> float:
    """A --min-* value: a number from 0 to 1.

    Raises:
        argparse.ArgumentTypeError: anything else, nan included, which compares
            false to both bounds and so would let every rate through the gate.
    """
    try:
        value = float(text)
    except ValueError:
        value = None
    if value is None or not 0 <= value <= 1:
        raise argparse.ArgumentTypeError(f"needs a number from 0 to 1, got {text!r}")
    return value


if __name__ == "__main__":
    sys.exit(main())
