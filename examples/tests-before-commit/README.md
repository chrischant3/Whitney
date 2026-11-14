# Tests-before-commit example

A project with one rule about the order of the agent's actions, and the eval that tests it with the `ran_before` check:

- `AGENTS.md`: run the tests with `python3 -m unittest` before every commit, and commit only when they pass. Kilo reads it, and so does Claude Code when the project has no `CLAUDE.md`.
- `kilo.jsonc`: an empty Kilo config, which the `kilo` loader needs to stage the project.
- `evals/agents-md.json`: two prompts that ask for a change and a commit. Each expects `ran_before` with `first` matching a test run where a command starts, `then` matching `git commit` (with or without `-C <folder>`), and `first_must_pass` set, so a failing test run before the commit doesn't count. Its baseline, the default, removes `AGENTS.md`.
- `evals/fixtures/till/`: a tiny till with one passing test.

Run every command below from the root of the Whitney checkout. Check the eval without calling a model:

    whitney validate --project-path examples/tests-before-commit --evals-path examples/tests-before-commit/evals

It ends `1 eval file(s), 0 problem(s), 0 warning(s)` and exits 0.

Run it (this calls a real model and costs money; about 3 cents a run on Haiku through the Kilo Gateway, so one epoch of both scenarios in both variants is about 14 cents):

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/tests-before-commit --evals-path examples/tests-before-commit/evals --epochs 1

The recorded run, of the first scenario only, is in [docs/results/2026-11-13-ran-before.md](../../docs/results/2026-11-13-ran-before.md): the template ran the tests before committing, and the baseline committed with no tests.
