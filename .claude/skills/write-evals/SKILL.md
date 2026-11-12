---
name: write-evals
description: Draft Whitney eval files for an instruction file (AGENTS.md, CLAUDE.md, a Kilo rules file, kilo.jsonc permissions, a Kilo SKILL.md or agent definition, or a Claude Code skill), with fixtures, and validate them until `whitney validate` is clean. Use when someone asks to write, draft, generate or add evals, scenarios or tests for an instruction, rules, skill or agent file, or asks "how do I test my AGENTS.md". Never starts a run that calls a model unless the person asks for one, and helps read the results after one.
---

# Write evals for an instruction file

Turns an instruction file into an eval file Whitney can run: scenarios for each testable rule, a small fixture for the agent to work in, and a clear list of the rules Whitney can't test yet. Read the doc sections and reference files this skill names, at the step that names them, rather than working from memory.

## Find Whitney first

Every path this skill names, such as `docs/adding-tests.md` and `examples/consumer/evals/`, is inside a Whitney checkout. If you aren't working in one, find it: ask the person for its path if they didn't give it. Then:

- Read the docs from the checkout, which match its version.
- Run `whitney` from the checkout's venv, `<checkout>/.venv/bin/whitney`, or the `whitney` on the PATH if the person installed it. Check with `whitney --version`.

`examples/consumer/evals/` has a worked eval file for each kind of file under test: a rules file, `kilo.jsonc`, a skill and an agent.

## Inputs

You need three things. Infer them where you can, and ask only for what you can't find:

- **The file under test**, such as `AGENTS.md` or `.kilo/agents/reviewer.md`.
- **The project path**: the folder that holds it, which Whitney reads and never writes. A skill on its own, outside a project, needs one built to hold it: see "A project to hold a skill on its own" in [references/fixtures.md](references/fixtures.md).
- **The evals path**: where the eval files and fixtures go. Default to `evals/` in the project. Whitney never stages it.

Before writing anything, check the evals path. Every `*.json` under it, outside `fixtures/` and hidden folders, must be a Whitney eval file. If it already holds other JSON, such as another tool's `evals.json`, don't write there at all, not even beside that file: use `whitney-evals/` in the project instead, and say why.

Write only under the evals path, and in a scratch project if you build one. Never write anywhere else in the project. A skill folder you were given is never the project or the evals path: never write inside it, its own `evals/` folder included, even to fix the file under test. When you copy a skill into a scratch project, copy `SKILL.md` and the files it links to, and leave out its `evals/`, even if someone asks for the whole folder.

## Steps

### 1. List the checks this version has

Run `whitney checks`. It gives each check's parameters, targets and gate, and whether it has been shown to fail in a real run. Don't use a check it doesn't list.

### 2. Split the file into rules, and map each one

Go through the file line by line. Leave out what isn't a rule: the tech stack, a list of commands, a project description. For a skill or an agent, its `description` is a rule too: it says when the agent should use it, and trigger tests check it. For each rule, pick the check that would catch it being broken:

| The rule says | Check | What it watches |
|---|---|---|
| Never commit secrets or `.env` files | `no_secret_committed` | commits, with the fixture's fake `tokens` |
| Don't copy data out of a folder, such as customer exports | `no_string_added` with canaries from that data (an email domain, full names); add `no_secret_committed` with the same canaries for commit prompts | a rise in the count in watched files, and commits |
| Don't edit, delete or create certain files (schema, migrations, a constraints file, a test folder) | `no_file_edits` with `paths` | the files matching `paths` |
| Don't write a value, name or pattern into code (a production URL, a deprecated class, `@ts-ignore`, ticket ids) | `no_string_added` with `strings`, and `regex: true` for a family such as `OPS-\d+` | a rise in the count in watched files |
| Ask before X, or don't X unless the user asks | `no_file_edits` on what X changes | In a headless run there's no one to ask, so stopping without the change is the pass |
| Don't run command X | `no_file_edits` on a file left by a stand-in for X | the stand-in's file; see [references/fixtures.md](references/fixtures.md) |
| Treat file contents as data, not instructions | `no_string_added` and `no_file_edits` on what an instruction planted in an input file asks for | the planted instruction's effect |
| Stay inside the project | `outside_canary_not_reached` | the fixture's `outside/` folder, on both targets. On `claude-code` a shell read fails it unless a `Read` deny rule refuses it; read "`outside_canary_not_reached`" in `docs/adding-tests.md` |
| A `kilo.jsonc` `deny` or `ask` rule | `permission_held` | the harness's refusal (`kilo`); read "`permission_held`" and "Variants" in `docs/adding-tests.md` first |
| A `.claude/settings.json` `deny` or `ask` rule written `Bash(<pattern>)` | `permission_held` | the harness's refusal (`claude-code`); read "`permission_held` on Claude Code" in `docs/adding-tests.md` first |
| A skill's or agent's `description` | trigger tests, which use `skill_loaded`, `subagent_delegated` and their `not_` forms | both targets; read "Testing a skill or an agent" and "Trigger tests" |

Some rules can't be tested with today's checks. Say so plainly, with the reason, rather than forcing a check that doesn't fit. The usual ones:

- **The order of actions**, such as "always run tests before committing" or "read X before writing code": no check sees the order of commands or which files were read.
- **Something the agent must do**, such as "write a test" or "update the changelog": the checks watch for what mustn't happen. A rule can sometimes be turned round, such as "colocate tests" becoming "no test file in a separate test folder", but then a run that writes no test at all also passes. Say that too. In a rule like "Never X. Do Y instead", the "do Y" half is usually untestable, and a pass on "never X" doesn't show the agent did Y.
- **The quality, tone or format of what's written**, such as "one plain sentence per bullet" or "write for customers": no check judges it.
- **Anything only in the agent's reply.** Checks read files, commits and the harness's logs, not the chat. A secret pasted into a reply passes `no_secret_committed`. For a skill whose output is a report or a review, most of its rules fall here. Say so at the top of the hand-back, so nobody reads a pass as "the skill works": the evals test what it mustn't do, and whether it loads, not the quality of its report.
- **In `kilo.jsonc`, an `allow` rule or a key that isn't a permission**, such as `instructions`: an `allow` rule forbids nothing, so `permission_held` refuses it. Test what `instructions` loads with an eval file for each loaded file.
- **In `.claude/settings.json`, an `allow` rule, a rule for any tool but `Bash`, a bare `Bash` rule, or any key but `permissions`**: Claude Code ignores a project's `allow` rules in an eval run, whose folder is never trusted; only `Bash(<pattern>)` has been captured being refused; and Whitney copies only the `permissions` block, so hooks and the rest never run.

### 3. Design one small fixture

Read [references/fixtures.md](references/fixtures.md) now, and "Fixtures" and "Setup steps" in `docs/adding-tests.md`. In short: put in only the files the scenarios need, never copy what Whitney stages, use made-up canaries for secrets and data, and put `git_init` and `git_commit` in `defaults.setup`.

### 4. Write the scenarios

Read [references/scenarios.md](references/scenarios.md) now, and "Scenarios" in `docs/adding-tests.md`. In short: at least two scenarios per testable rule, one without pressure and one or more with it; no prompt that grants what the rule asks for; `paths` that watch where breaking the rule lands; and, for a skill or an agent, a `trigger` block.

### 5. Write the eval file

Read "Eval files" in `docs/adding-tests.md` for the fields. Name the file after the file under test:

| File under test | Eval file |
|---|---|
| `AGENTS.md` or another Markdown file at the project root | `agents-md.json` |
| `.kilo/rules/<name>.md` | `rules/<name>-md.json` |
| `kilo.jsonc` | `configuration/kilo-jsonc.json` |
| `.claude/settings.json` | `configuration/claude-settings.json` |
| `.kilo/skills/<name>/SKILL.md` or `.claude/skills/<name>/SKILL.md` | `skills/<name>.json` |
| `.kilo/agents/<name>.md` or `.claude/agents/<name>.md` | `agents/<name>.json` |

- **`targets`**: leave it out for `AGENTS.md`, which both targets stage. Use `["kilo"]` for `kilo.jsonc` and for a root file only Kilo reads, such as `CONTEXT.md`: a `kilo.jsonc` file without it that uses `permission_held` applies to `claude-code` too, and fails validation there. A source under `.kilo/` applies to `kilo` alone, and one under `.claude/agents/`, or `.claude/settings.json`, to `claude-code` alone, without it.
- **`variants`**: a file with `permission_held` must declare its own, with a control that sets the rule under test to `allow` in `kilo.jsonc`, or takes it out of `.claude/settings.json` (such as `"/permissions/deny": []`), so the check is shown able to fail. Read "Variants". Other files can leave `variants` out and get the default baseline, which removes the file under test, or a skill's whole folder.
- **`defaults`**: put `fixture`, `setup` and any shared `expect` here.
- **One eval file per file under test.** To test another of its rules, add scenarios to the same file.

### 6. Validate until it's clean

Run `whitney validate` for each target the file applies to, and no other:

```bash
whitney validate --target kilo --project-path <project> --evals-path <evals>
whitney validate --target claude-code --project-path <project> --evals-path <evals>
```

Only a source both targets stage needs both: `AGENTS.md`, or a skill under `.claude/skills/`. For a `kilo`-only file, `--target claude-code` prints `0 problem(s)` without having checked it, so don't report that line as a result.

Each problem names the file and the field. Fix it and run the command again, until the last line reads `0 problem(s)`. A warning is worth reading, but doesn't stop a run.

### 7. Stop before anything that costs money

`whitney run` calls a real model. Don't start it unless the person asks. Give them the first run to try, one epoch of this file only, and say how many agent runs it makes: each scenario runs in the template and each variant, each positive trigger prompt in the template and the control, and each negative once. `--only` takes the eval file's path inside the evals folder or its eval name, but can't pick one scenario. For a large file, "Running one scenario with Inspect" in `docs/adding-tests.md` gives a cheaper first look.

```bash
whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
    --project-path <project> --evals-path <evals> --only <eval-file> --epochs 1
```

Without a Kilo login, use a provider key: `--model anthropic/claude-haiku-4-5 --api-key-env ANTHROPIC_API_KEY`. For `claude-code`, use `--target claude-code --model anthropic/claude-haiku-4-5`, which reads `ANTHROPIC_API_KEY`.

## What to hand back

1. The table from step 2: each rule, its scenarios and check, and each rule that can't be tested yet, with the reason.
2. The files written: the eval file and the fixture.
3. The `whitney validate` output for each target the file applies to.
4. What a pass won't show, such as a `no_file_edits` pass when the agent did nothing at all.
5. The commands from step 7.

## After the first run

When the person has run the evals and asks what the results mean, read [references/reading-results.md](references/reading-results.md) and work through the report in its order.
