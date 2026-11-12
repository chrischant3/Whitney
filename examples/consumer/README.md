# Consumer example

A project with its own rules, and the evals that test them, laid out as a team using Whitney with both Kilo and Claude Code would keep them:

- `AGENTS.md`: never edit files under `vendor/`. Kilo reads it. Tested by `evals/agents-md.json` with the `no_file_edits` check, on both targets.
- `CLAUDE.md`: the same rule, and the two data-handling rules below, word for word. Claude Code reads `CLAUDE.md` in place of `AGENTS.md` when both are there, so the rules have to be in it. They are repeated, not imported with `@AGENTS.md`. When the example was written Whitney didn't copy imported files; it has since 2026-11-12, so an import would work too ([load-order.md](../../docs/load-order.md#imports)). On `claude-code`, `evals/agents-md.json` tests its `vendor/` rule (its `AGENTS.md` source stands for the instruction files Claude Code reads), and `evals/claude-md.json` tests its data-handling rules with `no_string_added` and `no_secret_committed`. Change a rule in both places. The report still names `AGENTS.md` as the file under test there and lists it under "Not Staged"; the rule it ran with is `CLAUDE.md`'s.
- `.kilo/rules/data-handling.md`, loaded through `instructions` in `kilo.jsonc`: never copy customer data out of `exports/`, and never point code at production URLs. Tested by `evals/rules/data-handling-md.json`, on `kilo`, with `no_string_added` and `no_secret_committed`.
- `.kilo/skills/release-notes/`: a skill for writing release notes, which must leave internal ticket ids out of `CHANGELOG.md`. Tested on `kilo` by `evals/skills/release-notes.json`: its scenarios use `no_string_added`, and its trigger tests ask whether the right prompts load the skill. A second skill, `debugging`, is there so one trigger test can check that a bug-fix prompt loads it and not `release-notes`.
- `.claude/skills/release-notes/` and `.claude/skills/debugging/`: the same two skills, copied for Claude Code, which doesn't read `.kilo/skills/`. Tested on `claude-code` by `evals/skills/claude-release-notes.json`. Keep the two copies the same. Kilo reads `.claude/skills/` too, so a Kilo run holds both copies. In the recorded one-epoch run Kilo loaded the `.kilo/` copy each time both were there ([results](../../docs/results/2026-11-12-claude-code-example.md#which-skill-copy-kilo-used)). The Kilo eval's baseline removes both, and so does the [trigger control](../../docs/adding-tests.md#trigger-tests) Whitney adds (a run with the skill taken out, where a positive prompt must fail): on `kilo` it removes every staged `release-notes` skill folder ([details](../../docs/adding-tests.md#testing-a-skill-or-an-agent)). In the first recorded run it removed only `.kilo/skills/release-notes/`, and both control prompts passed by loading the `.claude/skills/` copy; after the fix, a one-epoch re-run on Kilo failed both, as it should ([results](../../docs/results/2026-11-12-claude-code-example.md#the-kilo-trigger-control-re-run-after-the-fix)).
- `.kilo/agents/reviewer.md`: a reviewer that reports problems and never changes a file. Tested on `kilo` by `evals/agents/reviewer.json`: its scenarios run it with `kilo run --agent` and use `no_file_edits`, and its trigger tests ask whether a request for a review is handed to it.
- `.claude/agents/reviewer.md`: the same reviewer for Claude Code. Claude Code doesn't read `.kilo/agents/`, and Kilo doesn't read `.claude/agents/`. Its frontmatter has a `name`, which Claude Code needs, and no `mode`, which is Kilo's. Tested on `claude-code` by `evals/agents/claude-reviewer.json`. Claude Code has no `--agent` for a run, so its scenarios ask for the reviewer subagent by name; `no_file_edits` sees the main agent's edits too, so a pass means nobody changed a `.py` file. The eval file switches off Claude Code's built-in `code-review` skill: in the recorded run the model handed both scenarios to it and not to this reviewer. With that off, the reviewer changed nothing and the main agent made the edit itself, so the scenarios still fail: they pass only when nobody edits the file, whoever it is.
- `kilo.jsonc`: a permission rule, `"git push *": "deny"`, so nobody pushes from an agent's session. Tested by `evals/kilo-jsonc.json` with the `permission_held` check. Its control variant sets the rule to `allow`, and there the push must go through. The file says `"targets": ["kilo"]`.
- `.claude/settings.json`: the same rule for Claude Code, `"deny": ["Bash(git push:*)"]`. It is a real settings file: a Claude Code session started in this folder is refused `git push` too. Tested by `evals/claude-settings.json` with the `permission_held` check, on `claude-code` alone. Its control variant, `push_allowed`, empties the `deny` list, and there the push must go through. It doesn't add an `allow` rule, because Claude Code ignores a project's `allow` rules in an eval run ([results](../../docs/results/2026-11-12-claude-code-permissions.md)). Whitney copies only this file's `permissions` block.
- `evals/`: the eval files and their fixtures. Whitney never stages this folder, so the agent under test can't read the evals.

Why a second file for `CLAUDE.md`: `agents-md.json` stays the eval for the project's instructions on both targets. But on Kilo the data-handling rules live in `.kilo/rules/data-handling.md`, and the eval file for that source can't run on `claude-code`, which doesn't copy `.kilo/`. On Claude Code the same rules are in `CLAUDE.md`, so they need an eval file whose source Claude Code does copy. That is `claude-md.json`, for `claude-code` alone.

Which eval file runs on which target, and the file it tests there:

| Eval file | Tests | `kilo` | `claude-code` |
|---|---|---|---|
| `agents-md.json` | the `vendor/` rule | `AGENTS.md` | `CLAUDE.md` |
| `claude-md.json` | the data-handling rules | not run | `CLAUDE.md` |
| `rules/data-handling-md.json` | the data-handling rules | `.kilo/rules/data-handling.md` | not run |
| `skills/release-notes.json` | the skill | `.kilo/skills/release-notes/` | not run |
| `skills/claude-release-notes.json` | the skill's copy | not run | `.claude/skills/release-notes/` |
| `agents/reviewer.json` | Kilo's reviewer | `.kilo/agents/reviewer.md` | not run |
| `agents/claude-reviewer.json` | Claude Code's reviewer | not run | `.claude/agents/reviewer.md` |
| `kilo-jsonc.json` | the push rule | `kilo.jsonc` | not run |
| `claude-settings.json` | the push rule | not run | `.claude/settings.json` |

The Kilo loader stages the root `CLAUDE.md` too, so the Kilo baselines of `agents-md.json` and `rules/data-handling-md.json` remove `CLAUDE.md` as well as the file under test. Without that, a copy of the rule would still be in the baseline.

Run every command below from the root of the Whitney checkout.

Check the evals without calling a model:

    whitney validate --target kilo --project-path examples/consumer --evals-path examples/consumer/evals
    whitney validate --target claude-code --project-path examples/consumer --evals-path examples/consumer/evals
    whitney validate --project-path examples/consumer --evals-path examples/consumer/evals

Each ends `9 eval file(s), 0 problem(s), 0 warning(s)` and exits 0. The count is every eval file in the folder; each target checks the files in its column above.

Run them (this calls a real model and costs money; `claude-code` needs `ANTHROPIC_API_KEY`). One epoch is 37 agent runs per target and cost about $1.30 a target on Haiku in the [recorded run](../../docs/results/2026-11-12-claude-code-example.md#what-it-cost), so the three epochs below are about $4 a target:

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/consumer --evals-path examples/consumer/evals --epochs 3
    whitney run --target claude-code --model anthropic/claude-haiku-4-5 --project-path examples/consumer --evals-path examples/consumer/evals --epochs 3

Expect exit 1 today, not a broken setup: in the recorded one-epoch run three files on each target were below their minimum ([the rates](../../docs/results/2026-11-12-claude-code-example.md#the-rates)). The example shows rules that hold only some of the time.

Every eval file runs with the whole project copied in, so the rules' evals run with the skills and the reviewer present too. A skill added later can change an older eval's result: the `debugging` skill fits the prompt "tests/test_titles.py has a failing test", for one.

Every customer email in the fixture ends `@canary-7d41c2.invalid`, a domain that can't exist, so the check can find a copied row anywhere.
