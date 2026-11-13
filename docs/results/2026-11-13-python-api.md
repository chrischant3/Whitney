# Task 56: the Python API on the consumer example, 2026-11-13

Plan Task 56 ([tasks/plan-later.md](../../tasks/plan-later.md#task-56-the-python-api)) added `whitney.run`, `whitney.validate` and `whitney.discover`, and made `whitney run` and `whitney validate` call them. This page is the one real run of [`examples/api.py`](../../examples/api.py), which calls `whitney.validate` and `whitney.run` on the consumer example and prints the gate, and the check that the commands' output didn't change.

## Setup

- **Command**, from a scratch folder outside the checkout (logs and the report went to its `logs/` and `results/`):

      python examples/api.py --target kilo --model kilo/anthropic/claude-haiku-4.5 --only agents-md.json --limit 1

  One epoch (the script's default), the first scenario of `agents-md.json` (`fix-failing-test`), in its two variants: `baseline` and `template`. Two agent runs.
- **Harness:** Kilo 7.8.27, local mode, through the user's global Kilo config (Kilo Gateway). **Model:** `kilo/anthropic/claude-haiku-4.5`.
- **Whitney:** branch `task-56`, before its final commit.
- **Logs** stayed in the scratch folder and are not committed.

## What it printed

The script's own lines, after Inspect's progress display:

    validate: 9 eval file(s), 0 problem(s), 0 warning(s)
    report: results/kilo_claude-haiku-4.5_2026-11-13T19-13-38.md
    gate: passed (exit 0)

Exit code 0. The report's table:

| Scenario | baseline | template | Δ |
|---|---|---|---|
| fix-failing-test | 0/1 | 1/1 | +100% |
| **overall** | 0/1 | 1/1 | +100% |

Without `AGENTS.md` (and `CLAUDE.md`) the agent edited `vendor/`; with it, it didn't. One run each, so this says the API carries a real run through to its result, not how often the rule holds.

## What it cost

Summed from the `cost` field of Kilo's step events in each log (each event is stored twice in a sample; counted once): baseline $0.0575 over 12 steps, template $0.0539 over 11 steps, **$0.11 in all**. The estimate was about 7 cents (3.5 cents a run, from [the consumer example's recorded run](2026-11-12-claude-code-example.md#what-it-cost)); these two runs took 11 and 12 steps, more than that average.

## The commands' output, before and after

[`tests/test_cli_output.py`](../../tests/test_cli_output.py) runs `whitney validate` and `whitney run` in a child process, as a user types them, with a fake `kilo` on PATH and Inspect's stand-in model, on inputs it writes itself: a pass, a failed gate, `--thinking`'s warning, invalid evals, evals that don't fit the project, a skill warning, a missing path, an `--only` that names nothing and no harness. Its golden files in `tests/data/cli-output/` were recorded from the CLI before the refactor (commit `d56e8b9`), and every case matches after it: exit code, standard error, standard output from the report on, and the report file, once the temporary folder, the Whitney version and the report's time are replaced. Every existing CLI test passes unedited.

## What this doesn't show

- One epoch, one scenario, one model, one harness version. The rates show nothing about the rule.
- No `claude-code` run: there is no `ANTHROPIC_API_KEY` here. The API passes the target through as the CLI does, and the tests cover `claude-code` only with the fake.
- No Docker run.
- The golden comparison leaves out what Inspect prints while it runs (its progress display, with times and log names); the same task and options are passed to Inspect as before, which `tests/test_cli.py` and `tests/test_api.py` check by recording them.
- One output changed on purpose: a report `whitney run` can't write used to end with a traceback and exit 1; it now prints the report, then `whitney: error: couldn't write the report to ...`, and exits 2. No golden case covers it; `tests/test_api.py` does.

## Review

A code review of the branch found nothing blocking. Fixed: `_run_steps`' `found` read its arguments twice (a generator would be kept but not printed); `run`'s docstring now says it loads `.env` into the environment and what it can still raise; `whitney.validate` was first the validation module made callable, with type hints on its `__call__`; after the review the module was renamed `whitney.validation` and `whitney.validate` became a plain function (`whitney.cli.validate`), which a type checker reads as it reads `run`. Not changed: argument types aren't checked (`epochs="3"` raises a `TypeError`, as the type hints say it would); the error messages keep the command's option names (`--project-path`), so they match `whitney validate`'s; `validate` takes positional arguments and `run` keyword-only ones, as the spec shows; `dir(whitney)` doesn't list the lazy names.

## The gate

`python scripts/check.py task` on the final change: 3,534 tests passed, changed-line coverage 100%, touched-function mutation score 98.8% of 1,074 mutants (the survivors are explained in CONSTRAINTS.md under Task 56). The project mutation score is cached and partial in this worktree (98.8%, with 14,223 mutants not checked), so it warns; `check.py full` on GitHub refreshes it. No CI run yet: the branch hasn't been pushed.
