"""Whitney's Python API on the consumer example: validate it, run it, and print the gate.

    python examples/api.py --target kilo --model kilo/anthropic/claude-haiku-4.5 --only agents-md.json --limit 1

It does what `whitney validate` and `whitney run` do, from Python:
`whitney.validate` returns the problems and warnings instead of printing them,
and `whitney.run` returns the exit code, the report, where it was written and
why the gate failed, instead of exiting. A run calls a real model and costs
money: the command above is two agent runs (the baseline and the template),
a few cents on Haiku. The logs go to ./logs and the report to ./results.

Exit code: the run's (0 the gate passed, 1 it failed, 2 it couldn't run), or
1 if validation found a problem, in which case nothing runs.
"""

import argparse
from pathlib import Path

import whitney

CONSUMER = Path(__file__).resolve().parent / "consumer"


def main(argv: list[str] | None = None) -> int:
    """Validate the consumer example for the target, run it if it's valid, and print the gate; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--target", required=True, choices=("kilo", "claude-code"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--only", nargs="+", metavar="FILE_OR_NAME", help="run only these eval files")
    parser.add_argument("--limit", type=int, help="at most this many scenarios per eval file and variant")
    parser.add_argument("--epochs", type=int, default=1)
    args = parser.parse_args(argv)

    found = whitney.validate(CONSUMER, CONSUMER / "evals", target=args.target)
    for problem in found.problems:
        print(f"error: {problem}")
    for warning in found.warnings:
        print(f"warning: {warning}")
    print(f"validate: {len(found.files)} eval file(s), {len(found.problems)} problem(s), {len(found.warnings)} warning(s)")
    if found.problems:
        return 1

    result = whitney.run(
        target=args.target, model=args.model, project_path=CONSUMER, evals_path=CONSUMER / "evals",
        only=args.only, limit=args.limit, epochs=args.epochs,
    )
    if result.error:
        print(f"couldn't run: {result.error}")
    if result.report_path:
        print(f"report: {result.report_path}")
    print(f"gate: {'passed' if result.exit_code == 0 else 'failed' if result.exit_code == 1 else 'not judged'} (exit {result.exit_code})")
    for failure in result.gate_failures:
        print(f"  - {failure}")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
