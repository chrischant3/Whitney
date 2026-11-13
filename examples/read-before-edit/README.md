# Read-before-edit example

A project with one rule about what the agent reads before it edits, and the eval that tests it with the `read_before_edit` check:

- `AGENTS.md`: your first action in every task is to read `CONSTRAINTS.md`. Kilo reads it, and so does Claude Code when the project has no `CLAUDE.md`.
- `kilo.jsonc`: an empty Kilo config, which the `kilo` loader needs to stage the project.
- `evals/agents-md.json`: two prompts that ask for a change to `till.py`, the second one asking the agent to look at files only through the shell. Each expects `read_before_edit` with `file` matching `CONSTRAINTS.md`. Its baseline, the default, removes `AGENTS.md`.
- `evals/fixtures/till/`: a tiny till with a `CONSTRAINTS.md` and one passing test.

Run every command below from the root of the Whitney checkout. Check the eval without calling a model:

    whitney validate --project-path examples/read-before-edit --evals-path examples/read-before-edit/evals

It ends `1 eval file(s), 0 problem(s), 0 warning(s)` and exits 0.

Run it (this calls a real model and costs money; about 3 cents a run on Haiku through the Kilo Gateway, so one epoch of both scenarios in both variants is about 12 cents):

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/read-before-edit --evals-path examples/read-before-edit/evals --epochs 1

The recorded runs are in [docs/results/2026-11-13-read-before-edit.md](../../docs/results/2026-11-13-read-before-edit.md). The baseline edited `till.py` without reading `CONSTRAINTS.md` every time. The template read it first only with the rule's current wording; two softer wordings ("read it before you edit") were ignored.
