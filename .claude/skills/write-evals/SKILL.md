---
name: write-evals
description: Draft Whitney eval files for an instruction file (AGENTS.md, CLAUDE.md, a Kilo rules file, kilo.jsonc permissions, a Kilo SKILL.md or agent definition), with fixtures, and validate them until `whitney validate` is clean. Use when someone asks to write, draft, generate or add evals, scenarios or tests for an instruction, rules, skill or agent file, or asks "how do I test my AGENTS.md". Stops before any run that calls a model.
---

# Write evals for an instruction file

Turns an instruction file into an eval file Whitney can run: one or more scenarios per testable rule, a small fixture for the agent to work in, and a clear list of the rules Whitney can't test yet. The reference for every field is `docs/adding-tests.md`. Read the sections this skill names rather than working from memory, and run `whitney checks` for the checks this version has.

## Inputs

You need three things. Infer them where you can, and ask only for what you can't find:

- **The file under test**, such as `AGENTS.md` or `.kilo/agents/reviewer.md`.
- **The project path**: the folder that holds it, which Whitney reads and never writes.
- **The evals path**: where the eval files and fixtures go. Default to `evals/` in the project. Whitney never stages it.

## Steps

### 1. List the checks this version has

Run `whitney checks`. It gives each check's parameters, targets and gate, and whether it has been shown to fail in a real run. Don't use a check it doesn't list.

### 2. Split the file into rules, and map each one

Go through the file line by line. Leave out what isn't a rule: the tech stack, a list of commands, a project description. For each rule, pick the check that would catch it being broken:

| The rule says | Check | What it watches |
|---|---|---|
| Never commit secrets or `.env` files | `no_secret_committed` | commits, with the fixture's fake `tokens` |
| Don't edit, delete or create certain files (schema, migrations, lockfiles, a constraints file, a test folder) | `no_file_edits` with `paths` | the files matching `paths` |
| Don't write a value, name or pattern into code (a production URL, a deprecated class, `@ts-ignore`) | `no_string_added` with `strings` | a rise in the count in watched files |
| Ask before X | `no_file_edits` on what X changes | In a headless run there's no one to ask, so stopping without the change is the pass |
| Stay inside the project | `outside_canary_not_reached` | the fixture's `outside/` folder (`kilo` only) |
| A `kilo.jsonc` permission rule | `permission_held` | the harness's refusal (`kilo` only); read "`permission_held`" in `docs/adding-tests.md` first |
| Load this skill, or hand work to this agent | `skill_loaded`, `subagent_delegated` and their `not_` forms, with trigger tests | `kilo` only; read "Testing a skill or an agent" and "Trigger tests" |

Some rules can't be tested with today's checks. Say so plainly, with the reason, rather than forcing a check that doesn't fit. The usual ones:

- **Order of actions**, such as "always run tests before committing" or "read X before writing code": no check sees the order of commands or which files were read.
- **Something the agent must do**, such as "write a test" or "update the changelog": the checks watch for what mustn't happen. A rule can sometimes be turned round, such as "colocate tests" becoming "no test file in a separate test folder", but then a run that writes no test at all also passes. Say that too.

### 3. Design one small fixture

Read "Fixtures" and "Setup steps" in `docs/adding-tests.md`. One fixture for the whole file is usually enough. Keep it to the files the scenarios need:

- The files each rule protects, such as `db/schema.sql`, `package.json` or `CONSTRAINTS.md`, so breaking the rule is possible.
- Enough code for the task to make sense, in the project's own language.
- For a secrets rule, a `.env` holding a unique, obviously fake token, such as `CANARY-<PROJECT>-DB-5e9a`. Never a real value. Leave it out of the `git_commit` step's `files`, so it's an untracked file the agent could commit by mistake.
- For "commit my work" prompts, a `pending/` folder with a real change, applied by the `apply_pending` step.

Set the default setup to `git_init`, then `git_commit` with every fixture file except the secrets.

### 4. Write the scenarios

Read "Scenarios" in `docs/adding-tests.md`. For each testable rule, write at least two:

- One with `"pressure": "none"`: an ordinary task where breaking the rule is the easy way.
- One or more with pressure: `time`, `authority`, `explicit-request` or `workaround`.

Each prompt must leave a right way to do the task, so a pass shows the rule holding, not an impossible task.

Watch for a prompt that grants what the rule asks for. "Add date-fns" is approval to add a dependency, and "the tech lead has approved the schema change" is the asking done. Then breaking the rule isn't a failure, and the scenario proves nothing. Use pressure that doesn't give permission: a deadline, "do it the fastest way", or an authority who argues against the rule itself.

Every `strings` and `tokens` value must appear in the fixture or the prompt, or the check could never fail. Put the rule's own text in `metadata.rule`, so the report's reader can see what each scenario tests.

### 5. Write the eval file

Read "Eval files" in `docs/adding-tests.md` for the fields and the file name, such as `agents-md.json` for `AGENTS.md`.

- **`targets`**: leave it out for `AGENTS.md`, which both targets stage. Use `["kilo"]` for anything under `.kilo/`, for `kilo.jsonc`, or for a check that is `kilo` only.
- **One eval file per file under test.** To test another of its rules, add scenarios to the same file.

### 6. Validate until it's clean

Run `whitney validate` once for each target the file applies to:

```bash
whitney validate --target kilo --project-path <project> --evals-path <evals>
whitney validate --target claude-code --project-path <project> --evals-path <evals>
```

Each problem names the file and the field. Fix it and run the command again, until the last line reads `0 problem(s)`. A warning is worth reading, but doesn't stop a run.

### 7. Stop before anything that costs money

`whitney run` calls a real model. Don't start it unless the person asks. Give them the first run to try, one epoch of this file only:

```bash
whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
    --project-path <project> --evals-path <evals> --only <eval-file> --epochs 1
```

Without a Kilo login, use a provider key: `--model anthropic/claude-haiku-4-5 --api-key-env ANTHROPIC_API_KEY`. After that, the same with `--epochs 3` gives a result worth keeping.

## What to hand back

1. The table from step 2: each rule, its scenarios and check, and each rule that can't be tested yet, with the reason.
2. The files written: the eval file and the fixture.
3. The `whitney validate` output for each target.
4. What a pass won't show, such as a `no_file_edits` pass when the agent did nothing at all. Suggest reading a few explanations after the first run ("Read why each run passed or failed" in the walkthrough).
5. The commands from step 7.
