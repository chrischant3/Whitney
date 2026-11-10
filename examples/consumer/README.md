# Consumer example

A project with its own rules, and the evals that test them, laid out as a team using Whitney would keep them:

- `AGENTS.md`: never edit files under `vendor/`. Tested by `evals/agents-md.json` with the `no_file_edits` check.
- `.kilo/rules/data-handling.md`, loaded through `instructions` in `kilo.jsonc`: never copy customer data out of `exports/`, and never point code at production URLs. Tested by `evals/rules/data-handling-md.json` with `no_string_added` and `no_secret_committed`.
- `.kilo/skills/release-notes/`: a skill for writing release notes, which must leave internal ticket ids out of `CHANGELOG.md`. Tested by `evals/skills/release-notes.json`: its scenarios use `no_string_added`, and its trigger tests ask whether the right prompts load the skill. A second skill, `debugging`, is there so one trigger test can check that a bug-fix prompt loads it and not `release-notes`.
- `.kilo/agents/reviewer.md`: a reviewer that reports problems and never changes a file. Tested by `evals/agents/reviewer.json`: its scenarios run it with `kilo run --agent` and use `no_file_edits`, and its trigger tests ask whether a request for a review is handed to it.
- `evals/`: the eval files and their fixtures. Whitney never stages this folder, so the agent under test can't read the evals.

Check the evals without calling a model:

    whitney validate --target kilo --project-path examples/consumer --evals-path examples/consumer/evals

Run them (this calls a real model):

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/consumer --evals-path examples/consumer/evals --epochs 3

Every customer email in the fixture ends `@canary-7d41c2.invalid`, a domain that can't exist, so the check can find a copied row anywhere.
