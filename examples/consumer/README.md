# Consumer example

A project with its own rules, and the evals that test them, laid out as a team using Whitney would keep them:

- `AGENTS.md`: never edit files under `vendor/`. Tested by `evals/agents-md.json` with the `no_file_edits` check.
- `.kilo/rules/data-handling.md`, loaded through `instructions` in `kilo.jsonc`: never copy customer data out of `exports/`, and never point code at production URLs. Tested by `evals/rules/data-handling-md.json` with `no_string_added` and `no_secret_committed`.
- `.kilo/skills/release-notes/`: a skill for writing release notes, which must leave internal ticket ids out of `CHANGELOG.md`. Tested by `evals/skills/release-notes.json`: its scenarios use `no_string_added`, and its trigger tests ask whether the right prompts load the skill. A second skill, `debugging`, is there so one trigger test can check that a bug-fix prompt loads it and not `release-notes`.
- `.kilo/agents/reviewer.md`: a reviewer that reports problems and never changes a file. Tested by `evals/agents/reviewer.json`: its scenarios run it with `kilo run --agent` and use `no_file_edits`, and its trigger tests ask whether a request for a review is handed to it.
- `kilo.jsonc`: a permission rule, `"git push *": "deny"`, so nobody pushes from an agent's session. Tested by `evals/kilo-jsonc.json` with the `permission_held` check. Its control variant sets the rule to `allow`, and there the push must go through. The file says `"targets": ["kilo"]`.
- `.claude/settings.json`: the same rule for Claude Code, `"deny": ["Bash(git push:*)"]`. It is a real settings file: a Claude Code session started in this folder is refused `git push` too. Tested by `evals/claude-settings.json` with the `permission_held` check, on `claude-code` alone. Its control variant, `push_allowed`, empties the `deny` list, and there the push must go through. It doesn't add an `allow` rule, because Claude Code ignores a project's `allow` rules in an eval run ([results](../../docs/results/2026-11-12-claude-code-permissions.md)). Whitney copies only this file's `permissions` block.
- `evals/`: the eval files and their fixtures. Whitney never stages this folder, so the agent under test can't read the evals.

Check the evals without calling a model:

    whitney validate --target kilo --project-path examples/consumer --evals-path examples/consumer/evals
    whitney validate --target claude-code --project-path examples/consumer --evals-path examples/consumer/evals

Each ends `6 eval file(s), 0 problem(s), 0 warning(s)`. On `claude-code`, `agents-md.json` and `claude-settings.json` apply; the other four files are for Kilo.

Run them (this calls a real model; `claude-code` needs `ANTHROPIC_API_KEY`):

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/consumer --evals-path examples/consumer/evals --epochs 3
    whitney run --target claude-code --model anthropic/claude-haiku-4-5 --project-path examples/consumer --evals-path examples/consumer/evals --epochs 3

Every eval file runs with the whole project copied in, so the two rules' evals now run with the skills and the reviewer present too. A skill added later can change an older eval's result: the `debugging` skill fits the prompt "tests/test_titles.py has a failing test", for one.

Every customer email in the fixture ends `@canary-7d41c2.invalid`, a domain that can't exist, so the check can find a copied row anywhere.
