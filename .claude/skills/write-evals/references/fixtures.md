# Fixtures

Read this before designing the fixture (step 3 of the skill), alongside "Fixtures" and "Setup steps" in `docs/adding-tests.md`. One fixture for the whole eval file is usually enough.

## What Whitney stages, so the fixture doesn't need it

Don't copy the file under test, `kilo.jsonc`, `AGENTS.md` or anything else under `.kilo/` into the fixture, or their text. Whitney stages those from the project into each run, all of them, not only the file under test. "Evaluating a local project" in `docs/adding-tests.md` lists which files each target stages.

Read what else gets staged. Another rule or skill may push the agent the other way, and a skill's negative trigger prompts can name another staged skill as their `owner`.

## What goes in

Keep the fixture to the files the scenarios need:

- **The files each rule protects**, such as `db/schema.sql`, `package.json` or `CONSTRAINTS.md`, so breaking the rule is possible.
- **Enough code for the task to make sense**, in the project's own language. If the project has no code to copy, use the language its instruction files name, or ask.
- **For a secrets or data rule**, the secret or data itself, with unique, made-up values: a `.env` holding `CANARY-<PROJECT>-DB-5e9a`, or an export whose emails use a domain such as `cust-91e3.invalid`. Never copy a value from a real `.env` or other secret file. If the rule is about values that look real, a made-up value in a realistic shape is fine, but avoid a real provider's key prefix, which secret scanners flag.
- **For "commit my work" prompts**, a `pending/` folder with a real change, which the `apply_pending` step writes over the project.
- **For a permission rule**, almost nothing: the agent only has to try the forbidden call. Add a `pending/` change if the prompt asks it to commit first.
- **For a rule against running a command**, a stand-in for the command: a small script in the fixture that writes a file, such as `logs/run.txt`, and exits. Name it in the fixture's README, so the agent finds it. The run then needs no real command installed, and a `no_file_edits` check on the file catches the rule being broken. Only calls by that name are seen.
- **For a skill with an `owner` negative**, enough for the other skill's task to make sense, such as a failing test for a debugging skill. If the project has no other skill, you may add one the user really uses beside it, but say so: it's staged in every variant, so it changes what is tested.
- **For input files shaped like instruction files**, such as the `AGENTS.md` an audit skill reviews, rename them, wherever they sit in the fixture, such as `proposed-rules/AGENTS.v1.md`. A file named `AGENTS.md` or `CLAUDE.md` may be loaded by the harness as its own instructions, including one in a subfolder when the agent works there. Then the run tests the harness obeying the file, not the agent reading it.
- **For a rule about treating files as data**, an instruction planted in an input file that asks for something with its own visible effect, such as adding a canary comment to a source file. Watch only that effect, so a report that quotes the instruction still passes.

## What makes a Kilo run end early

A Kilo run has no person to answer a permission request, so Kilo rejects it and the run ends. Every check on that run is then inconclusive: the harness decided, not the agent. Kilo 7.8.27 asks, by default:

- **Before reading any `*.env` or `*.env.*` file**, apart from `*.env.example`. A secrets fixture is fine, but write prompts that don't need the agent to read the `.env`. Committing it doesn't read it; "check the database URL in .env" does.
- **Before reaching outside the project**, such as a temp folder. If the task needs somewhere to build, name a folder inside the fixture in the prompt, such as `scratch/`.
- **Before editing some instruction files**, such as an `AGENTS.md` or a file under `.kilo/`. Don't write a task whose right answer is to edit one.

`kilo debug agent code` lists the permissions in force. A run with many inconclusive results is the fixture's fault, not the rule's: fix the prompts and run again.

## Setup

Put the default setup in `defaults.setup`: `git_init`, then `git_commit` with every fixture file except the secrets or data the agent might commit by mistake.

- A scenario's own `setup` replaces the default whole. A scenario that adds `apply_pending` must repeat the `git_init` and `git_commit` steps before it.
- Trigger tests take their fixture and setup from `defaults` only, so `defaults.fixture` is required for a file with a `trigger` block.

## A project to hold a skill on its own

A skill that isn't in a project needs one to hold it. Build it in a scratch folder:

- Put the skill at `.kilo/skills/<name>/`, with the folder named as the skill's `name`. Kilo also reads `.claude/skills/` and `.agents/skills/`, but only a skill under `.kilo/skills/` can have trigger tests.
- Add a `kilo.jsonc`. The `kilo` target needs one, and `{}` is enough.
- Copy `SKILL.md` and the files it links to, such as `references/`. Leave out any `evals/`, tests or expected answers, even if someone asks for the whole folder. Whitney stages a skill's whole folder into every run, so another tool's `evals/` inside it would put its answers in front of the agent.

A Claude Code skill, from `.claude/skills/`, can be tested only this way, on the `kilo` target. The `claude-code` target stages no skills. Say in the hand-back that the result shows how the skill does under Kilo, not under Claude Code.

Copied reference material, such as a doc the skill reads, goes stale when the original changes. Copy it again before each run.
