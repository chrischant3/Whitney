"""The `whitney` command: run a project's evals against a harness and model, or check them without a model.

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \\
        --project-path . --evals-path evals --epochs 3
    whitney validate --project-path . --evals-path evals [--target kilo] [--constraints CONSTRAINTS.md]
    whitney report logs [--evals-path evals]      # render a report from saved logs; calls no model
    whitney report logs --junit whitney.xml       # run and report also write JUnit XML (whitney.junit)
    whitney checks                                # list the checks an eval file can name

Exit codes, for every subcommand (docs/specs/package-and-expected-results.md):

| Code | `whitney run` | `whitney validate` |
|---|---|---|
| 0 | validation passed and every gate met (warnings allowed) | valid (warnings allowed) |
| 1 | a gate failed, or an Inspect log isn't `success` | a validation problem was found |
| 2 | couldn't run: bad arguments, a missing path, an invalid eval file, an unset credential variable, or the harness CLI not found | couldn't run: bad arguments or a missing path |

`whitney run` validates everything, checks the credential variable it was
named and runs `kilo --version` (or `claude --version`) before any sample,
so each of those fails with a 2 and a message rather than a run of errored
samples. `whitney report` applies a run's gate to the logs it reads: 0, 1 if
a gate failed or a log isn't `success`, and 2 if it couldn't run (the folder
is missing, or holds no log or one it can't read). `whitney checks` exits 0.
There is no default for the two paths, the target or the model: a
consumer who forgot one would otherwise get a green run that tested
something else. Whitney reads no CI-specific environment variable, so the
same invocation works on any CI system.

scripts/run_evals.py, the deprecated runner, shares this module's report
and gate (write_report) and its option parsing (rate, min_rates).

`run` and `validate` are the Python API behind `whitney run` and `whitney
validate`, re-exported from `whitney`: they return what the commands print.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TextIO

from inspect_ai import eval as inspect_eval
from inspect_ai.log import list_eval_logs, read_eval_log

# Inspect loads a .env file from the current folder (or above) when an eval
# starts; loading it the same way first lets the credential check below see
# what the run will see. Inspect has no public name for this.
from inspect_ai._util.dotenv import init_dotenv

from whitney import targets
from whitney.agent import redact
from whitney.capture import thinking_warnings
from whitney.capture_report import render_capture_markdown
from whitney.checks import CheckSpec, registry
from whitney.credentials import CredentialsError, resolve_gateway_credentials
from whitney.evidence import run_secrets
from whitney.evalfile import EvalFile, Problem
from whitney.improve import improvement_failed, improvement_report
from whitney.junit import junit_xml
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
from whitney.tasks import _whitney_version, scenario_eval, why_not_applies
from whitney.thresholds import MIN_ADHERENCE, MIN_CONCLUSIVE, MIN_HELD
from whitney.validation import (
    ValidationResult,
    permission_warnings,
    skill_copy_warnings,
    skill_warnings,
    validate_evals,
    validate_project,
)
from whitney.variants import TEMPLATE, declared_variants

EXECUTIONS = ("local", "docker")
# The CLI each target runs, checked with `<name> --version` before a run.
HARNESS_COMMANDS = {"kilo": "kilo", "claude-code": "claude"}
MAX_VERSION_LENGTH = 200  # a version is short; a long first line is something else, kept out of every report
# In Docker mode Inspect answers for the model: the key variable of each provider the bridge serves, besides kilo/.
DOCKER_KEY_VARIABLES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
VERSION_TIMEOUT = 30  # seconds; `--version` answers at once, so a longer wait means a hung CLI
SAMPLE_SHUFFLE_SEED = 42  # as run_evals.py: the same order across variants and runs
BROKEN_INSTALL = "Whitney's dependencies don't fit together, so reinstall them from requirements.lock (README, \"Setup\")"  # added to the stop line of a run that hit an ImportError

# Help texts, kept out of the parser's code so each option reads as one line there.
HELP = {
    "run": "run the eval files against a harness and model, print and save the report, and gate on it",
    "validate": "check the eval files and the project without calling a model",
    "report": "render the report from Inspect logs already written, and gate on it, without calling a model",
    "checks": "list every check an eval file can name, with its parameters, outcomes, targets and minimum",
    "LOGS": "the folder holding the Inspect logs, such as logs",
    "report --evals-path": "the evals root the logs were run from, so the report can say if an eval file has changed since",
    "--target": "the harness under test",
    "--model": "the model under evaluation, as provider/model, e.g. kilo/anthropic/claude-haiku-4.5",
    "--project-path": "the project whose config and instruction files are under test; it is read, never written",
    "--evals-path": "the evals root: the eval files, plus their fixtures in fixtures/",
    "--only": "run only these eval files: paths relative to the evals root, or eval names",
    "--constraints": "a CONSTRAINTS.md-style file; each known_gap's ID must be a row in its Exceptions table",
    "--execution": "local, or docker: the harness in a container with no network, served by Inspect's model bridge",
    "--api-key-env": "local mode: the variable (shell or .env) holding the API key for --model's provider; "
    "kilo without it uses your global Kilo config, claude-code defaults to ANTHROPIC_API_KEY",
    "--base-url": "kilo local mode: base URL for --model's provider (e.g. a proxy)",
    "--capture-prompt": (
        "record what was sent to the model in each run, and say in the report whether each file's source text was in it: "
        "locally through a proxy on 127.0.0.1, in Docker from Inspect's log of every model call. Off by default"
    ),
    "--thinking": (
        "tell the harness to think and show it (kilo: --variant high --thinking; claude-code: showThinkingSummaries; "
        "Docker: Inspect's reasoning_tokens too). The log and report give what Whitney told the harness, and the level "
        "the run's first model call carried: with --capture-prompt, or in Docker, read from that request; without "
        "--capture-prompt a local run records only what Whitney told the harness. On kilo/ models Kilo's Gateway route "
        "didn't apply it in real runs (2026-11-12); a warning says so. Off by default"
    ),
    "--epochs": "runs of each scenario",
    "--limit": "at most this many scenarios per eval file and variant",
    "--sample-shuffle": f"shuffle the scenarios reproducibly (seed {SAMPLE_SHUFFLE_SEED}, the same for every variant)",
    "--max-samples": "harness runs at once; Docker mode also needs enough memory",
    "--log-dir": "where Inspect's logs go",
    "--results-dir": "where the Markdown report goes",
    "--junit": "also write the results as JUnit XML to this file, for a CI system to show: a test suite per eval file, "
    "a test case per check and one for the gate (README, \"JUnit XML for your CI system\")",
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
    """The `whitney` command's argument parser, with the `run`, `validate`, `report` and `checks` subcommands."""
    p = argparse.ArgumentParser(prog="whitney", description=__doc__.split("\n")[0])
    p.add_argument("--version", action="version", version=f"%(prog)s {_whitney_version() or 'not installed'}")
    commands = p.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help=HELP["run"], description=HELP["run"])
    run.add_argument("--target", choices=TARGETS, required=True, help=HELP["--target"])
    _add_paths(run)
    run.add_argument("--model", required=True, help=HELP["--model"])
    run.add_argument("--only", nargs="+", metavar="FILE_OR_NAME", help=HELP["--only"])
    run.add_argument("--execution", choices=EXECUTIONS, default="local", help=HELP["--execution"])
    run.add_argument("--api-key-env", help=HELP["--api-key-env"])
    run.add_argument("--base-url", help=HELP["--base-url"])
    run.add_argument("--capture-prompt", action="store_true", help=HELP["--capture-prompt"])
    run.add_argument("--thinking", action="store_true", help=HELP["--thinking"])
    run.add_argument("--epochs", type=int, default=3, help=HELP["--epochs"])
    run.add_argument("--limit", type=int, help=HELP["--limit"])
    run.add_argument("--sample-shuffle", action="store_true", help=HELP["--sample-shuffle"])
    run.add_argument("--max-samples", type=int, default=2, help=HELP["--max-samples"])
    run.add_argument("--log-dir", default="logs", help=HELP["--log-dir"])
    run.add_argument("--results-dir", default="results", help=HELP["--results-dir"])
    run.add_argument("--junit", metavar="FILE", help=HELP["--junit"])
    run.add_argument("--min-rate", action="append", default=[], metavar="CHECK=VALUE", help=HELP["--min-rate"])
    run.add_argument("--min-conclusive", type=rate, default=MIN_CONCLUSIVE, help=HELP["--min-conclusive"])
    run.add_argument("--min-adherence", type=rate, help=HELP["--min-adherence"])
    run.add_argument("--min-held", type=rate, help=HELP["--min-held"])
    run.set_defaults(handler=_run, parser=run)
    validate = commands.add_parser("validate", help=HELP["validate"], description=HELP["validate"])
    validate.add_argument("--target", choices=TARGETS, help=HELP["--target"] + "; left out, every target a file applies to")
    _add_paths(validate)
    validate.set_defaults(handler=_validate)
    report = commands.add_parser("report", help=HELP["report"], description=HELP["report"])
    report.add_argument("logs", metavar="LOGS", help=HELP["LOGS"])
    report.add_argument("--evals-path", help=HELP["report --evals-path"])
    report.add_argument("--results-dir", default="results", help=HELP["--results-dir"])
    report.add_argument("--junit", metavar="FILE", help=HELP["--junit"])
    report.add_argument("--min-rate", action="append", default=[], metavar="CHECK=VALUE", help=HELP["--min-rate"])
    report.add_argument("--min-conclusive", type=rate, default=MIN_CONCLUSIVE, help=HELP["--min-conclusive"])
    report.set_defaults(handler=_report, parser=report)
    checks = commands.add_parser("checks", help=HELP["checks"], description=HELP["checks"])
    checks.set_defaults(handler=_checks)
    return p


def _add_paths(p: argparse.ArgumentParser) -> None:
    """The options both subcommands take besides --target: the two paths and the constraints file."""
    p.add_argument("--project-path", required=True, help=HELP["--project-path"])
    p.add_argument("--evals-path", required=True, help=HELP["--evals-path"])
    p.add_argument("--constraints", help=HELP["--constraints"])


def _check_args(args: argparse.Namespace) -> None:
    """What argparse can't check alone: --min-rate's pairs and --limit, and `whitney report`'s folders.

    `run` and `validate` check their own paths first (_check_paths), so a
    call from Python gets the same message as the command.

    Raises:
        SystemExit: through the subcommand's parser, for a bad option value.
        CannotRun: a `whitney report` folder that isn't one, or a --junit path that can't be written (_clear_junit).
    """
    _clear_junit(getattr(args, "junit", None))  # only run and report have --junit
    if args.command == "report":
        args.min_rate = min_rates(args.parser, args.min_rate)
        if not Path(args.logs).is_dir():
            raise CannotRun(f"{args.logs} isn't a folder")
        if args.evals_path is not None and not Path(args.evals_path).is_dir():
            raise CannotRun(f"--evals-path {args.evals_path} isn't a folder")
        return
    if args.command == "run":
        args.min_rate = min_rates(args.parser, args.min_rate)
        if args.limit is not None and args.limit < 1:
            args.parser.error("--limit must be positive")
        # Inspect waits forever with max_samples 0, and refuses epochs below 1 only once the run has started.
        for option, value in (("--epochs", args.epochs), ("--max-samples", args.max_samples)):
            if value < 1:
                args.parser.error(f"{option} must be positive")


def _check_paths(project_path: str | Path, evals_path: str | Path, constraints: str | Path | None) -> None:
    """Raise CannotRun, naming the option, if either path isn't a folder or the constraints file isn't a file."""
    for option, folder in (("--project-path", project_path), ("--evals-path", evals_path)):
        if not Path(folder).is_dir():
            raise CannotRun(f"{option} {folder} isn't a folder")
    if constraints is not None and not Path(constraints).is_file():
        raise CannotRun(f"--constraints {constraints} isn't a file")


def _clear_junit(path: str | None) -> None:
    """Make --junit's folder and remove a file an earlier run left there, before anything runs.

    A run that stops without writing the file then leaves none, rather than an
    earlier run's that a CI system would show, perhaps green. Writing a test
    file there and removing it again finds a path that can't be written before
    a paid run, not after it.

    Raises:
        CannotRun: the path is a folder, or it or its folder can't be made or written.
    """
    if path is None:
        return
    junit = Path(path)
    try:
        junit.parent.mkdir(parents=True, exist_ok=True)
        if junit.is_dir():
            raise IsADirectoryError(f"{path} is a folder")
        junit.unlink(missing_ok=True)
        junit.touch()
        junit.unlink()
    except OSError as e:
        raise CannotRun(f"--junit {path} can't be written: {e}") from e


def _validate(args: argparse.Namespace) -> int:
    """`whitney validate`: print every problem and warning `validate` finds, then a count; 1 if there is a problem."""
    result = validate(args.project_path, args.evals_path, args.target, args.constraints)
    _print_findings(result)
    print(f"{Path(args.evals_path)}: {len(result.files)} eval file(s), {len(result.problems)} problem(s), {len(result.warnings)} warning(s)")
    return 1 if result.problems else 0


def validate(
    project_path: str | Path, evals_path: str | Path, target: str | None = None, constraints: str | Path | None = None
) -> ValidationResult:
    """Check the eval files, and the project against them, without calling a model: `whitney validate` as a function.

    The project is checked for `target`, or for every target when it is
    None, against the eval files that apply to that target. For the kilo
    target it also warns about the project's skill descriptions
    (validation.skill_warnings), whether or not an eval file tests a skill,
    about permission rules (validation.permission_warnings), and about a
    declared variant that leaves a copy of its skill staged
    (validation.skill_copy_warnings). Nothing is printed.

    Args:
        project_path: the project whose config and instruction files are under test; read, never written.
        evals_path: the evals root: the eval files, plus their fixtures in fixtures/.
        target: "kilo" or "claude-code"; None checks every target a file applies to.
        constraints: a CONSTRAINTS.md-style file; each known_gap's ID must be a row in its Exceptions table.

    Returns:
        The problems and warnings, each a Problem(file, location, message),
        and the eval files that loaded. Any problem means the evals aren't valid.

    Raises:
        CannotRun: a path that isn't a folder, a constraints file that isn't
            a file, or a target that doesn't exist (`whitney validate` exits 2).
    """
    _check_paths(project_path, evals_path, constraints)
    if target is not None:
        _check_target(target, "local")
    evals, project = Path(evals_path), Path(project_path)
    result = validate_evals(evals, constraints=Path(constraints) if constraints else None)
    checks = registry()
    for each in (target,) if target else TARGETS:
        applicable = [f for f in result.files if why_not_run(f, each, checks) is None]
        if applicable:
            result.problems += validate_project(applicable, project, each, evals_path=evals)
        if each == "kilo":  # only Kilo runs stage skills, and only Kilo's permission rules are tested
            result.warnings += skill_warnings(project, evals_path=evals)
            result.warnings += permission_warnings(applicable, project, evals_path=evals)
            result.warnings += skill_copy_warnings(applicable, project, evals_path=evals)
    return result


def _report(args: argparse.Namespace) -> int:
    """`whitney report`: read the logs in a folder and report on them as a run would, calling no model.

    The report's target and model are the logs' own (report_target, report_model).

    It applies a run's gate to the logs it reads, and knows no more than they
    hold: not which eval files a run expected and never logged, nor whether
    it was stopped early, nor the minimums it was given, which must be given
    again. Logs of several runs in one folder are counted together.

    Raises:
        CannotRun: the folder holds no Inspect log, or one that can't be read.
    """
    logs = []
    for info in list_eval_logs(args.logs):
        try:
            logs.append(read_eval_log(info.name))
        except Exception as e:  # whatever a damaged log raises: the report couldn't be made, which isn't a failed gate
            raise CannotRun(f"couldn't read the log {info.name}: {type(e).__name__}: {e}") from e
    if not logs:
        raise CannotRun(f"no Inspect logs under {args.logs}")
    return write_report(
        logs, target=report_target(logs), model=report_model(logs), evals_path=args.evals_path,
        min_rates=replaced_minimums(None, None, args.min_rate), min_conclusive=args.min_conclusive, results_dir=args.results_dir,
        junit=args.junit,
    )


def report_model(logs: list, given: str | None = None) -> str:
    """The model a report on saved `logs` names: the one they all record, "mixed-models", or `given` if none records one.

    A Docker run's model is the log's own; a local run's is in its metadata
    or task arguments, as `model` or, in logs written before the rename, `kilo_model`.
    """
    model_ids = set()
    for log in logs:
        metadata = log.eval.metadata or {}
        task_args = log.eval.task_args or {}
        if metadata.get("execution") == "docker":
            candidate = getattr(log.eval, "model", None)
        else:
            candidate = (
                metadata.get("model") or metadata.get("kilo_model") or task_args.get("model") or task_args.get("kilo_model")
            )
        if candidate and candidate != "none/none":
            model_ids.add(str(candidate))
    if not model_ids:
        return given or "unknown-model"
    return model_ids.pop() if len(model_ids) == 1 else "mixed-models"


def report_target(logs: list, given: str | None = None) -> str:
    """The target a report on saved `logs` names: the one they all record, "mixed-targets", or `given` if there are no logs.

    Logs written before targets existed were all Kilo runs.
    """
    names = {(log.eval.metadata or {}).get("target", "kilo") for log in logs}
    if len(names) > 1:
        return "mixed-targets"
    return names.pop() if names else given or "unknown-target"


def _checks(args: argparse.Namespace) -> int:
    """`whitney checks`: print every registered check, by name, with what it declares."""
    print("\n\n".join(describe_check(spec) for _, spec in sorted(registry().items())))
    return 0


def describe_check(spec: CheckSpec) -> str:
    """One check as `whitney checks` lists it: what it judges, its targets, outcomes, minimum, parameters and proof.

    The last line names the write-up of a recorded real run that made the
    check fail (CheckSpec.proven), or says none has yet.
    """
    lines = [
        spec.name,
        f"  judges: {spec.judges}",
        f"  targets: {', '.join(spec.targets)}",
        f"  outcomes: {' / '.join(spec.labels)}",
        f"  minimum pass rate: {spec.min_pass_rate:.0%}",
    ]
    fields = spec.params.model_fields
    if fields:
        lines.append("  parameters:")
        for name, info in fields.items():
            lines.append(f"    {name}: {'required' if info.is_required() else _default_shown(info)}")
    else:
        lines.append("  parameters: none")
    lines.append(f"  shown to fail in a real run: {spec.proven or 'not yet'}")
    return "\n".join(lines)


def _default_shown(info) -> str:
    """A parameter's default as `whitney checks` prints it: as JSON where it can be, so it reads as an eval file would write it."""
    try:
        value = info.get_default(call_default_factory=True)
    except (TypeError, ValueError):  # a factory that needs the other parameters' values
        return "default computed from the other parameters"
    return f"default {json.dumps(value, default=str, ensure_ascii=False)}"


def _run(args: argparse.Namespace) -> int:
    """`whitney run`: `run` with the command's options, printing its findings to stderr as it goes, then its report.

    With --junit it then writes the results as JUnit XML (write_junit), and
    returns 2 if that file can't be written.

    Raises:
        CannotRun: `run` stopped before it had a report, or couldn't write it; its error is the message.
    """
    minimums = replaced_minimums(args.min_adherence, args.min_held, args.min_rate)
    done = run(
        target=args.target, model=args.model, project_path=args.project_path, evals_path=args.evals_path,
        only=args.only, constraints=args.constraints, execution=args.execution, api_key_env=args.api_key_env,
        base_url=args.base_url, capture_prompt=args.capture_prompt, thinking=args.thinking, epochs=args.epochs,
        limit=args.limit, sample_shuffle=args.sample_shuffle, max_samples=args.max_samples, log_dir=args.log_dir,
        results_dir=args.results_dir, min_rates=minimums, min_conclusive=args.min_conclusive, findings=sys.stderr,
    )
    if done.report is None:
        raise CannotRun(done.error)
    print(done.report)
    if done.report_path is None:
        raise CannotRun(done.error)
    print(f"Report written to {done.report_path}")
    if args.junit and not write_junit(
        args.junit, done.logs, done.gate_failures, min_rates=minimums, min_conclusive=args.min_conclusive
    ):
        return 2
    return done.exit_code


@dataclass
class RunResult:
    """What `run` did: what `whitney run` prints and writes, as values.

    Attributes:
        exit_code: `whitney run`'s: 0 every gate met, 1 a gate failed, 2 couldn't run.
        report: the Markdown report `whitney run` prints; None if the run stopped before it had one.
        report_path: the file under results_dir the report was written to; None if it has no report, or it couldn't be written.
        problems: what validation found wrong with the eval files or the project, as Problem(file, location,
            message); any one stops the run before it calls a model.
        warnings: what validation and the run's options warn about, which doesn't stop the run: a Problem,
            or a line about the whole run, such as --thinking's.
        gate_failures: why the gate failed, as the report's "Problems" section lists them; [] when it passed.
        error: why the run couldn't start, stopped partway with exit 2, or couldn't write its report; else None.
        logs: the Inspect logs of the tasks that ran.
    """

    exit_code: int
    report: str | None = None
    report_path: Path | None = None
    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem | str] = field(default_factory=list)
    gate_failures: list[str] = field(default_factory=list)
    error: str | None = None
    logs: list = field(default_factory=list)


def run(
    *,
    target: str,
    model: str,
    project_path: str | Path,
    evals_path: str | Path,
    only: list[str] | None = None,
    constraints: str | Path | None = None,
    execution: str = "local",
    api_key_env: str | None = None,
    base_url: str | None = None,
    capture_prompt: bool = False,
    thinking: bool = False,
    epochs: int = 3,
    limit: int | None = None,
    sample_shuffle: bool = False,
    max_samples: int = 2,
    log_dir: str | Path = "logs",
    results_dir: str | Path = "results",
    min_rates: Mapping[str, float] | None = None,
    min_conclusive: float = MIN_CONCLUSIVE,
    findings: TextIO | None = None,
) -> RunResult:
    """Run the eval files against a harness and model, write the report and gate on it: `whitney run` as a function.

    It validates everything, checks the credential variable it was named
    and runs `kilo --version` (or `claude --version`) before any sample, so
    a mistake returns exit code 2 and an error rather than a run of errored
    samples. It returns the exit code instead of exiting, and prints nothing
    of its own but the findings, and only to `findings`; Inspect prints its
    progress as it runs each task.

    Args:
        target, model, project_path, evals_path: as `whitney run`'s --target, --model, --project-path and --evals-path.
        only: run only these eval files: paths relative to the evals root, or eval names (--only).
        constraints, execution, api_key_env, base_url, capture_prompt, thinking, epochs, limit, sample_shuffle,
            max_samples, log_dir, results_dir, min_conclusive: as the options of the same names, with their defaults.
        min_rates: check -> the minimum pass rate replacing its default for this run (--min-rate).
        findings: where to print each problem and warning as it is found, as `whitney run` prints them to
            stderr; None prints none. They are in the result either way.

    Before checking credentials it loads a `.env` file from the current
    folder or the nearest one above it into os.environ, as Inspect does and
    `whitney run` always has.

    Returns:
        The exit code, the report and where it was written, what validation
        found, the gate's failures, the error if it couldn't run, and the logs.

    Raises:
        Whatever a fault outside Whitney's own checks raises before the first
        task, such as an OSError reading the constraints file, as `whitney
        run` did; everything Whitney checks is an exit code 2 with an error.
    """
    options = argparse.Namespace(
        target=target, model=model, project_path=project_path, evals_path=evals_path, only=only, constraints=constraints,
        execution=execution, api_key_env=api_key_env, base_url=base_url, capture_prompt=capture_prompt, thinking=thinking,
        epochs=epochs, limit=limit, sample_shuffle=sample_shuffle, max_samples=max_samples, log_dir=log_dir,
        results_dir=results_dir, min_rates=dict(min_rates or {}), min_conclusive=min_conclusive,
    )
    result = RunResult(exit_code=2)
    try:
        _check_run_options(options)
        _run_steps(options, result, findings)
    except CannotRun as e:
        result.error = str(e)
    return result


def _check_run_options(options: argparse.Namespace) -> None:
    """What `whitney run`'s parser and _check_args check, for a call from Python.

    Raises:
        CannotRun: a missing path, a count below 1, an unknown execution
            mode, or a minimum that isn't a registered check's or a number from 0 to 1.
    """
    _check_paths(options.project_path, options.evals_path, options.constraints)
    for name in ("limit", "epochs", "max_samples"):
        value = getattr(options, name)
        if value is not None and value < 1:
            raise CannotRun(f"{name} must be positive")
    if options.execution not in EXECUTIONS:
        raise CannotRun(f"execution must be {' or '.join(EXECUTIONS)}; got {options.execution!r}")
    checks = registry()
    for name, value in options.min_rates.items():
        if name not in checks:
            raise CannotRun(f"min_rates names no registered check {name!r}; the checks are {', '.join(sorted(checks))}")
        if not 0 <= value <= 1:
            raise CannotRun(f"min_rates needs a number from 0 to 1 for {name}, got {value!r}")
    if not 0 <= options.min_conclusive <= 1:
        raise CannotRun(f"min_conclusive needs a number from 0 to 1, got {options.min_conclusive!r}")


def _run_steps(args: argparse.Namespace, result: RunResult, findings: TextIO | None) -> None:
    """`run`'s work, into `result`: validate, check the credentials and the harness, build every task, then run them and gate.

    Raises:
        CannotRun: anything that stops the run before its first sample.
    """

    def found(problems: Iterable[Problem] = (), warnings: Iterable[Problem | str] = ()) -> None:
        problems, warnings = list(problems), list(warnings)  # read twice: kept, then printed
        result.problems.extend(problems)
        result.warnings.extend(warnings)
        if findings is not None:
            _print_findings(ValidationResult(problems=problems, warnings=warnings), findings)

    _check_target(args.target, args.execution)
    found(warnings=thinking_warnings(args.target, args.model, args.execution, args.capture_prompt, args.thinking))
    evals, project = Path(args.evals_path), Path(args.project_path)
    validated = validate_evals(evals, constraints=_constraints(args))
    found(validated.problems, validated.warnings)
    if validated.problems:
        raise CannotRun("the evals aren't valid; `whitney validate` lists the same problems")
    selected, not_run, left_out = _select(validated.files, evals, args.target, args.only)
    project_problems = validate_project(selected, project, args.target, evals_path=evals)
    found(project_problems)
    if project_problems:
        raise CannotRun("the evals don't fit the project; `whitney validate` lists the same problems")
    init_dotenv()
    missing = missing_credential(args.target, args.execution, args.api_key_env, model=args.model)
    if missing:
        raise CannotRun(missing)
    no_package = missing_package(args.target, args.execution)
    if no_package:
        raise CannotRun(no_package)
    # In Docker mode the harness is the image's, not the host's, so the host has nothing to check.
    secrets = run_secrets(args.target, args.execution, args.api_key_env, base_url=args.base_url)
    version = harness_version(args.target, secrets=secrets) if args.execution == "local" else None
    tasks = [
        _task(eval_file.path.relative_to(evals).as_posix(), variant, args, version)
        for eval_file in selected
        for variant in variant_order(eval_file)
    ]
    logs, stopped, cannot_run = [], None, False
    for done, task in enumerate(tasks):
        try:
            logs += inspect_eval(
                task,
                model=args.model if args.execution == "docker" else None,
                epochs=args.epochs,
                limit=args.limit,
                sample_shuffle=SAMPLE_SHUFFLE_SEED if args.sample_shuffle else None,
                max_samples=args.max_samples,
                log_dir=args.log_dir,
                display="plain",
                # Inspect keeps the API calls of a sample's first 5 model calls only, unless told otherwise.
                **({"log_model_api": True} if args.capture_prompt and args.execution == "docker" else {}),
            )
        except Exception as e:  # anything at all: the report still says what ran before it, and why it stopped
            stopped = f"stopped after {done} of {len(tasks)} tasks: {type(e).__name__}: {e}"
            if isinstance(e, ImportError):  # a dependency Inspect imports is missing or the wrong version
                stopped += f"; {BROKEN_INSTALL}"
            cannot_run = isinstance(e, (CannotRun, CredentialsError, ImportError))
            break
    result.logs = logs
    result.report, result.gate_failures = render_report(
        logs, target=args.target, model=args.model, evals_path=args.evals_path, min_rates=args.min_rates,
        min_conclusive=args.min_conclusive, not_run=not_run, left_out=left_out,
        expected=[f.eval_name for f in selected], stopped=stopped,
    )
    result.exit_code = 2 if cannot_run else 1 if result.gate_failures else 0
    result.error = stopped if cannot_run else None
    try:
        result.report_path = report_file(result.report, args.results_dir, args.model, args.target)
    except OSError as e:  # the report is still returned, and `whitney run` prints it before it says why it stopped
        result.exit_code, result.error = 2, f"couldn't write the report to {args.results_dir}: {e}"


def _check_target(target: str, execution: str) -> None:
    """Raise CannotRun, with targets.check_target's message, if the target can't run in the run's execution mode.

    A base URL the target can't use is refused when its tasks are built (_task).
    """
    try:
        targets.check_target(target, execution)
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
            **{name: True for name in ("capture_prompt", "thinking") if getattr(args, name)},
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
    """Why `eval_file` doesn't apply to `target`, or None if it does (tasks.why_not_applies, as its task would say).

    A check that isn't registered is left to validation.
    """
    named = dict.fromkeys(e.check for scenario in eval_file.resolved() for e in scenario.expect)
    return why_not_applies(target, eval_file.applies_to(), [checks[name] for name in named if name in checks])


def _select(
    files: list[EvalFile], evals: Path, target: str, only: list[str] | None
) -> tuple[list[EvalFile], dict[str, str], list[str]]:
    """The eval files to run; each file left out because it doesn't apply to `target`, with why; and each one --only left out.

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
    left_out = [path for path, why in reasons.items() if not why and path not in {f.path.relative_to(evals).as_posix() for f in chosen}]
    return selected, not_run, left_out


def variant_order(eval_file: EvalFile) -> list[str]:
    """`template` and the file's other variants in the report's order: baselines, template, controls; by name within each."""
    declared = declared_variants(eval_file)

    def rank(name: str) -> tuple[int, str]:
        return ROLE_ORDER.index(TEMPLATE if name == TEMPLATE else declared[name].role), name

    return sorted([TEMPLATE, *declared], key=rank)


def missing_credential(
    target: str,
    execution: str,
    api_key_env: str | None,
    environ: Mapping[str, str] = os.environ,
    *,
    model: str,
    auth_path: Path | None = None,
) -> str | None:
    """Why the run's credentials aren't usable, or None.

    A local claude-code run needs its key variable (api_key_env, default
    ANTHROPIC_API_KEY); a local kilo run needs api_key_env if it names one.
    A local kilo run without one uses the global Kilo config, which Whitney
    can't check without calling the model. In Docker mode Inspect answers
    for the model, so it needs the model's provider credentials: the Kilo
    Gateway's for a kilo/ model (credentials.resolve_gateway_credentials,
    reading the login at `auth_path` if given), else the provider's key
    variable (DOCKER_KEY_VARIABLES). A model the bridge can't serve, and
    api_key_env, are refused when the task is built.
    """
    if target == "claude-code" and execution == "local":
        name = api_key_env or "ANTHROPIC_API_KEY"
    elif execution == "local":
        name = api_key_env
    elif model.startswith("kilo/"):
        try:
            resolve_gateway_credentials(environ, auth_path)
        except CredentialsError as e:
            return str(e)
        return None
    else:
        name = DOCKER_KEY_VARIABLES.get(model.partition("/")[0])
    if name is None:
        return None
    return None if environ.get(name) else f"{name} is not set; put it in .env or the environment"


def missing_package(target: str, execution: str) -> str | None:
    """Why a Docker run of claude-code can't start for want of a package, or None.

    Inspect's bridge answers Claude Code with the `anthropic` package, which
    only the `gateway` extra installs. Without it the run would fail inside
    Inspect, after the image was built. A local run never needs it.
    """
    if target == "claude-code" and execution == "docker" and importlib.util.find_spec("anthropic") is None:
        return (
            "Docker mode for claude-code needs the `anthropic` package, which Inspect's bridge uses; "
            'install it with the gateway extra: pip install "whitney[gateway]"'
        )
    return None


def harness_version(target: str, secrets: Iterable[str] = ()) -> str:
    """The first line `<harness> --version` prints, such as "7.8.18" for Kilo, cut to MAX_VERSION_LENGTH characters.

    Args:
        target: the harness to check.
        secrets: the run's secret values (evidence.run_secrets), redacted
            from the stderr a failure quotes.

    Raises:
        CannotRun: the CLI isn't on PATH or can't be started, exits non-zero,
            prints nothing or doesn't finish within VERSION_TIMEOUT seconds.
    """
    command = HARNESS_COMMANDS[target]
    try:
        done = subprocess.run(
            [command, "--version"], capture_output=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL, timeout=VERSION_TIMEOUT,
        )
    except FileNotFoundError as e:
        raise CannotRun(f"`{command}` isn't on PATH; install the {target} target's CLI (see the README's Setup)") from e
    except OSError as e:  # found but not runnable, such as a file without execute permission
        raise CannotRun(f"`{command} --version` couldn't start: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise CannotRun(f"`{command} --version` didn't finish in {VERSION_TIMEOUT}s") from e
    if done.returncode != 0:
        # Redacted before it is cut, so a cut can't leave part of a secret behind.
        quoted = redact(done.stderr, list(secrets)).strip()[-300:]
        raise CannotRun(f"`{command} --version` exited {done.returncode}: {quoted}")
    lines = done.stdout.strip().splitlines()
    if not lines:
        raise CannotRun(f"`{command} --version` printed no version")
    return lines[0].strip()[:MAX_VERSION_LENGTH]


def write_report(
    logs: list, *, model: str, target: str, results_dir: str | Path, junit: str | Path | None = None, **report: object
) -> int:
    """Gate the logs, then print the report and write it to results_dir; return 1 if the gate failed, else 0.

    The report and gate are render_report's, given `logs`, `model`, `target`
    and the rest of its arguments in `report`; the file is report_file's.
    With `junit`, the results are also written there as JUnit XML
    (write_junit), and 2 is returned instead if that file can't be written.
    """
    text, problems = render_report(logs, model=model, target=target, **report)
    print(text)
    out = report_file(text, results_dir, model, target)
    print(f"Report written to {out}")
    if junit and not write_junit(
        junit, logs, problems, min_rates=report["min_rates"], min_conclusive=report["min_conclusive"]
    ):
        return 2
    return 1 if problems else 0


def write_junit(
    path: str | Path, logs: list, problems: list[str], *, min_rates: Mapping[str, float], min_conclusive: float
) -> bool:
    """Write the results as JUnit XML (whitney.junit) to `path`, making its folder, and print where.

    Args:
        path: the --junit file.
        logs: the Inspect logs the report was rendered from; only the core eval files' successful ones get a suite.
        problems: the gate's problems, as render_report returns them.
        min_rates: the check minimums replaced for this run.
        min_conclusive: the share of template runs that must be conclusive.

    Returns:
        False, after printing why to stderr, if the file can't be written: the
        Markdown report is written, but the file a CI system reads is missing, so the run can't count.
    """
    succeeded = [log for log in logs if not _benchmark(log.eval.task) and log.status == "success"]
    xml = junit_xml(succeeded, problems, min_rates=min_rates, min_conclusive=min_conclusive, timestamp=datetime.now())
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(xml, encoding="utf-8")
    except OSError as e:
        print(f"whitney: error: couldn't write the JUnit XML to {path}: {e}", file=sys.stderr)
        return False
    print(f"JUnit XML written to {path}")
    return True


def render_report(
    logs: list,
    *,
    target: str,
    model: str,
    evals_path: str | Path | None,
    min_rates: Mapping[str, float],
    min_conclusive: float,
    not_run: Mapping[str, str] | None = None,
    left_out: Iterable[str] = (),
    expected: Iterable[str] = (),
    stopped: str | None = None,
) -> tuple[str, list[str]]:
    """Gate the logs and render the report: the Markdown, and the gate's problems ([] when it passed).

    The gate fails on an Inspect log that isn't `success`, on any eval file
    whose template falls short (report.check_thresholds), on any `expected`
    eval file no log was read for, so a run that left no logs never passes,
    and on `stopped`. The report is the header (target, model, versions,
    eval files, any replaced minimum and a changed min_conclusive), one
    table per eval file, the IFEvalCode and Harbor tables if those ran, the
    files not run for this target and those --only left out, how to improve
    each file under test (whitney.improve, or one line naming the error if
    that section can't be built), what was sent to the model if any run
    captured it or its thinking (whitney.capture_report), the staged
    files' provenance and the gate's problems.

    Args:
        logs: Inspect logs, from this run or read back.
        target, model: what the report's first line names.
        evals_path: the evals root the logs' eval files are relative to.
        min_rates: the check minimums replaced for this run.
        min_conclusive: the share of template runs that must be conclusive.
        not_run: eval file -> why it doesn't apply to the target.
        left_out: eval files that apply but --only didn't name.
        expected: the eval names the run selected.
        stopped: why the run stopped before its last task, if it did.
    """
    core_logs = [log for log in logs if not _benchmark(log.eval.task)]
    ifeval_logs = [log for log in logs if log.eval.task.startswith("ifevalcode_")]
    harbor_logs = [log for log in logs if log.eval.task.startswith("bigcodebench_hard_complete_")]
    succeeded = [log for log in core_logs if log.status == "success"]
    records = [r for log in succeeded for r in records_from_log(log)]
    summary = summarise(records)
    failed_runs = [f"{log.eval.task}: {log.status}" for log in logs if log.status != "success"]
    problems = failed_runs + check_thresholds(summary, min_conclusive=min_conclusive, min_rates=min_rates)
    read = {s.file.eval_name if s.file else key for key, s in summary.items()}
    problems += [f"{name}: no conclusive runs: no log of its runs was read" for name in expected if name not in read]
    problems += [stopped] if stopped else []

    report = render_markdown(summary)
    if ifeval_logs:
        report += "\n" + render_ifevalcode_markdown(ifeval_logs)
    if harbor_logs:
        report += "\n" + render_harbor_markdown(harbor_logs)
    if not_run:
        report += "\n## Not run for this target\n\n" + "".join(f"- `{path}`: {why}\n" for path, why in not_run.items())
    if left_out:
        report += "\n## Left out by --only\n\n" + "".join(f"- `{path}`\n" for path in left_out)
    try:
        improvements = improvement_report(succeeded, min_conclusive, records)
    except Exception as error:  # anything at all: the section advises, so a fault in it costs neither report nor gate
        improvements = improvement_failed(error)
    report += f"\n{improvements}" if improvements else ""
    sent = render_capture_markdown(core_logs)
    report += f"\n{sent}" if sent else ""
    report += "\n" + render_asset_provenance_markdown(logs)
    header = render_header(logs, evals_path, min_rates)
    if min_conclusive != MIN_CONCLUSIVE:
        header += f"Minimum conclusive share for this run: {min_conclusive:g} (default {MIN_CONCLUSIVE:g}).\n\n"
    report = f"Target: `{target}` · Model: `{model}`\n\n" + header + report
    if problems:
        report += "\n## Problems\n\n" + "\n".join(f"- {p}" for p in problems) + "\n"
    return report, problems


def report_file(report: str, results_dir: str | Path, model: str, target: str) -> Path:
    """Write `report` to results_dir as <target>_<model>_<timestamp>.md, making the folder if need be, and return its path.

    Raises:
        OSError: the folder or the file can't be written.
    """
    timestamp = f"{datetime.now():%Y-%m-%dT%H-%M-%S}"
    out = Path(results_dir) / result_filename(timestamp, model, target)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    return out


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
        if name in rates:
            p.error(f"--min-rate gives {name} twice")
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
