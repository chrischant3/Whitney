# Plan: Phase 5, your own evals, skills and agents

| | |
|---|---|
| **Status** | Approved by Christopher on 2026-11-06: D1 to D9 and the recommendation for each decision below. Each task is built on its own branch off `main` (the `phase5` branch was retired on 2026-11-09). |
| **Written** | 2026-11-05, against commit `217230c` |
| **Builds** | The spec, [docs/specs/package-and-expected-results.md](../docs/specs/package-and-expected-results.md), plus three asks from 2026-11-05: users write evals for their own rules (first priority); skills and agents get evals, including tests of what triggers them; `--project-path` works for Claude Code projects (a bug). |
| **Tasks** | [todo-phase5.md](todo-phase5.md) |
| **Where it sits** | Phase 5 of [plan.md](plan.md). Its tasks stay in [todo-phase5.md](todo-phase5.md), not in [todo.md](todo.md), because other sessions may still be working on the open MVP tasks there (T9, T10). |

The spec in `docs/specs/` is an unchanged copy of `~/workspace/package-and-expected-results.md` as it stood at 23:38 on 2026-11-05. Edit the repo copy from now on.

## Where we are (2026-11-10)

Read this first when resuming. Also read `AGENTS.md` and `CONSTRAINTS.md`.

- **Status, 2026-11-11:** `v0.2.0` is tagged and released (2026-11-10). In `main`: Tasks 1 to 26 and Checkpoint C, and from Milestone E Tasks 34, 35 and 36 (PRs #40 to #44). Open on Task 26: the install from the tag, and a person's walkthrough. Not started: Tasks 27 to 32, 36a, 36b and 37 to 40; Task 33 has one box done. **Next:** 36b, then 36a, then 37. Each task not yet started now has an Evidence list (todo-phase5.md, "Evidence for every task not yet started"). The two bullets below marked 2026-11-10 are kept as a record.
- **In `main` (2026-11-10):** Tasks 1 to 22 and 25, Checkpoints A and B, validation rule 8 (PR #25), the release checklist, and the push-controls guide. Tasks 17 to 21 and 25 came in as PR #26.
- **Decided by Christopher on 2026-11-10 (Checkpoint B):**
  1. The four tests PR #26 changed, and the choices recorded beside Tasks 17 to 21 and 25, stand. One choice is reversed: the gate reads a file's scenarios and its trigger tests separately.
  2. Claude Code skills and agents come after `v0.2.0`.
  3. `MIN_HELD` stays at 0.9; Task 27 looks at raising it.
  4. `permission_held` covers pattern rules and `ask` only; Task 28 is a spike on evidence for a scalar deny.
  5. The packaging test is split: the wheel's contents in the default suite, the install in `check.py full` only.
- **Next, as it stood on 2026-11-10:** Task 26 (release `v0.2.0`), whose docs and version are merged; the release checklist is what remains. Checkpoint C was reviewed with Christopher on 2026-11-10 (todo-phase5.md). Tasks 23 (`permission_held`) and 24 (package data) are built.
- **Mutation testing (2026-11-10):** nothing skips it any more. Locally, run `check.py task`, which mutates the functions a change touches; the bar for those is 95%. `check.py full` runs on GitHub only (Christopher, 2026-11-10; CONSTRAINTS.md, "How to run"). The project score fell to 98.0% over Tasks 17 to 25 and PR #31 brought it back to 99.3%, inside the 0.5-point drift from the recorded 99.7%. The full run on GitHub confirmed 99.3% on `main` at `52b8592` (2026-11-10, exit 0). About 71 mutants survive, most of them equivalent and not yet listed in CONSTRAINTS.md. The numbered checks block from `WARN_UNTIL` (2026-11-16 in `scripts/check.py`).
- **What the model is sent (2026-11-10):** Kilo hands `AGENTS.md` to the model under a bare `Instructions from:` line, while Claude Code says it overrides the defaults. A Kilo plugin or a logging proxy can capture the request; OpenTelemetry, `kilo export` and the debug logs can't. Findings: [docs/results/2026-11-10-kilo-prompt-capture.md](../docs/results/2026-11-10-kilo-prompt-capture.md). Task 31 makes capture part of a run, with the agent's thinking. (2026-11-12: the Task 31 spike showed the proxy working on the Kilo Gateway route with `--pure` kept, and Claude Code's own `OTEL_LOG_RAW_API_BODIES` as a way with no proxy; [results](../docs/results/2026-11-12-prompt-capture-spike.md).)
- **A flaky test, not from this work:** `tests/test_parity.py::test_cli_resolves_the_shipped_template` runs the real `kilo debug config --pure`. That takes 1 to 4 seconds alone, but twice it hung past its 120-second limit inside `check.py`'s parallel run.
- **Decided 2026-11-09 (Christopher): build on `main`.** The `phase5` branch is retired. Each task gets its own branch and worktree off `main` and its own PR into `main`.
- **No longer in force (it ended on 2026-11-10, see "Mutation testing" above).** Decided 2026-11-09 (Christopher): skip mutation testing for now. It takes long enough to hold up every task. Until a spike finds a faster way, run `check.py task --skip-mutation` and `check.py full --skip-mutation`, which print a warning in place of the mutation scores, and say in each PR that mutation testing was skipped. CONSTRAINTS.md is unchanged: the mutation floor and ratchet stand, and the next run with mutation testing judges everything merged meanwhile. The spike is in `todo-phase5.md`, "Spike: faster mutation testing", and absorbs decision 2 below.
- **Decided 2026-11-08 (Christopher):**
  1. **Cloud sessions may push their own branches.** They can't be stopped from pushing; `main` is protected instead (PR required, signed commits, no direct pushes), and Christopher still reviews and merges every PR. Record this exception in `docs/agent-push-controls.md`.
  2. **Bring `check.py full` down from about 35 minutes (70 on a 4-core cloud machine) to 10 to 15 minutes**, in this order:
     - **Step 1, reuse results.** `full` currently deletes `mutants/mutmut-stats.json` (`2c24500`), because mutmut 3.8.1 only partly updates its test map when a test is added, so every full run re-tests all 6,874 mutants. Instead: rebuild the map, keep results for functions whose code and covering tests are unchanged, and re-test changed functions, functions whose covering tests changed, and every current survivor. Add a weekly from-scratch `check.py full` in GitHub Actions to catch a weakened test.
     - **Step 2, faster tests, not weaker ones.** From the stats map: `test_evidence.py::test_a_fake_agent_that_prints_its_key_and_writes_it_to_a_file_leaves_no_trace_in_the_log` (5.5 s alone) and `test_a_folder_over_the_file_cap_makes_a_real_runs_check_inconclusive` (2.5 s) are 56% of the worst-case mutation time; about 20 other end-to-end Inspect tests make up most of the rest. Profiling the first: Whitney's code takes about 30 ms; about 3.6 s is the event loop waiting on child processes. Find out what it waits for (a fixed poll interval in Inspect or in `agent.py`'s wait loop?) and cut it without changing what the test checks.
- **Mutation run time (2026-11-09, `claude/beautiful-albattani-5f1pgq`):** a 30 s per-test timeout on mutmut's runs halves a from-scratch run (31.4 to 16.0 min on a 4-core cloud machine) and its peak memory (14.6 to 7.3 GB), with the same survivors. 26 looping mutants had been 57% of the time, and 14 were OOM-killed and left out as segfaults. Options and next steps: [docs/mutation-testing-options.md](../docs/mutation-testing-options.md). Steps 1 and 2 above still stand.
- **Working rules:** `check.py task` locally, and `check.py full` on GitHub only; seed new worktrees' `mutants/` from the main checkout, `rm -rf mutants/tests`, touch the sources; use `.venv/bin/python`; git's `merge.ff=only` is set globally on Christopher's Mac, so merges there need `--no-ff`. Never commit `.env` files; never name a company, team or other repository in Whitney; changes to `floor_guard.py` or `check_approvals.py` need Christopher's signed commit.

## Overview

Today a user can add scenarios only for the two rules Whitney already knows how to score. After Phase 5 they can:

1. **Milestone A.** Write an eval file for any rule in JSON, using reusable checks, and run it against their own project from a clone.
2. **Milestone B.** Test a skill or an agent, including whether it triggers on the prompts it should and stays quiet on the ones it shouldn't.
3. **Milestone C.** Test any Kilo permission, not only `external_directory`.
4. **Milestone D.** Install Whitney as a package and run `whitney run` in their own CI.

The Claude Code `--project-path` bug is fixed first, on its own.

## How this plan differs from the spec

The spec is the design, and the plan follows it except for these changes. Each needs your approval. Once approved, the spec is updated to match.

| # | The spec says | The plan does | Why |
|---|---|---|---|
| D1 | The Claude Code loader is part of slice 4 | Task 1, first and alone | It's a bug, it's small, and it doesn't depend on schema v2 |
| D2 | Skill and subagent checks are release 3, blocked on a capture happening elsewhere (assumption 6) | Milestone B, straight after A, with Whitney's own capture (Task 16), which can run now | You made skills and agents a priority, and the capture needs only a few real runs |
| D3 | The `command` and `agent` scenario fields are release 2 | Milestone B (Task 20) | Testing what an agent does on its own needs `kilo run --agent` |
| D4 | Skill trigger tests use `"variants": {}` | Trigger prompts never run in a baseline. Positive prompts run in `template` and in an automatic control that removes the skill or agent; negative prompts run in `template` only. Triggers get their own report table. Behaviour scenarios in the same file keep their baseline | One eval file per source puts trigger and behaviour scenarios in the same file. Without the skill, every negative prompt would pass without testing anything. The control is what proves `skill_loaded` can fail, which the spec requires before a check is released ("Adding a Python check", rule 6) |
| D5 | Nothing | `whitney validate` warns when a skill has no description, or when two skills' descriptions are nearly the same (Task 19) | Kilo picks a skill by its description [K-skills]; the inspiration repo checks for near-collisions [A-cases]. It costs no model calls |
| D6 | Slice 7a (package) comes before 7b (command) | `whitney run`, `whitney validate` and discovery come in milestone A (Task 11). Moving the sandbox files into the package is milestone D (Task 24), and can run in parallel at any time | Users get their own evals working in a clone first, which is how the README describes using Whitney |
| D7 | `docs/adding-tests.md` is rewritten in slice 11 | Rewritten at the end of milestone A (Task 15) | Milestone A can't be used without it |
| D8 | "What's hard-coded today", Event fields row: the field naming the tool (`part.tool`) appears only in test fixtures | It's in real output. Two of the logs from 2026-11-03 (Kilo CLI, local mode, `kilo/anthropic/claude-haiku-4.5`) hold 74 tool parts, each with `part.tool` (`bash`, `read`, `write`) and `state.status` (`completed`, `error`) | The skill and subagent checks are less risky than the spec assumed. The `skill` and `task` tools have still never been seen, so Task 16 is still needed. Task 16 has since captured both (2026-11-06, `docs/spike-findings.md`) |
| D9 | Nothing on where an example skill or agent lives | In `examples/consumer/`, not `template/kilo/` | The spec's boundaries make adding to the template teams copy "ask first" |

## Task order

Tasks run in order inside each milestone. Three lanes can run beside the main one.

| Task | Title | Milestone | Depends on | Size | Real model |
|---|---|---|---|---|---|
| 1 | Fix: `--project-path` for the `claude-code` target | Bug fix | none | S | optional, cents |
| 2 | Check contract; port the two existing checks | A | none | M | no |
| 3 | Eval file schema v2, discovery, evals-only validation, version 1 upgrade | A | 2 | M | no |
| | **Checkpoint A1** | | 1–3 | | |
| 4 | Record golden data for the shipped scenarios | A | 3 | S | no |
| 5 | Setup steps and `pending/` fixtures | A | 4 | S | no |
| 6 | Variants: `remove` and `set` | A | 4 | S | no |
| 7 | Migrate the shipped eval files to version 2 | A | 5, 6 | S | no |
| 8 | Safe staging for every project path | A | 1, 7 | S | no |
| | **Checkpoint A2** | | 4–8 | | yes, one small run per target |
| 9 | Generic task, evidence and redaction | A | 8 | M | no |
| 10 | Report and gate keyed by eval file | A | 9 | M | no |
| 11 | `whitney run` and `whitney validate`; `run_evals.py` becomes a wrapper | A | 10 | M | no |
| 12 | Floor guard covers `cli.py` (your signed commit) | A | 11 | XS | no |
| | **Checkpoint A3** | | 9–12 | | |
| 13 | Checks: `no_string_added`, `no_file_edits` | A | 9 | M | no |
| 14 | Consumer example and a recorded real run | A | 11, 13 | S | yes |
| 15 | Docs for writing your own evals | A | 14 | S | no |
| | **Checkpoint A: your own rules** | | 13–15 | | |
| 16 | Capture: skills, subagents, `--agent` and `--command` in real Kilo | B | none | S | yes, cents |
| 17 | Checks: `skill_loaded`, `skill_not_loaded`, `subagent_delegated`, `subagent_not_delegated` | B | 9, 16 | M | no |
| 18 | The `trigger` block | B | 10, 17 | M | no |
| 19 | Skill description warnings in `whitney validate` | B | 11 | S | no |
| 20 | The `agent` and `command` scenario fields | B | 9, 16 | S | no |
| 21 | Example skill and agent, a recorded real run, and docs | B | 14, 17–20 | M | yes |
| | **Checkpoint B: skills and agents** | | 16–21 | | |
| 22 | Capture: refusal output for each permission (spec slice 9) | C | none | S | yes, cents |
| 23 | Check: `permission_held` | C | 9, 22 | M | yes |
| | **Checkpoint C: any Kilo permission** | | 22–23 | | |
| 24 | Package data, task registry, `whitney --version` | D | none | S | no |
| 25 | `whitney report` and `whitney checks` | D | 10, 11 | S | no |
| 26 | Docs and release `v0.2.0` | D | all above | S | no |
| | **Checkpoint D: release** | | 24–26 | | |
| 27 | Raise `MIN_HELD` to 1.0, or decide not to (after `v0.2.0`) | after D | 23 | S | no |
| 28 | Spike: evidence for a scalar deny (after `v0.2.0`) | after D | 23 | S | yes, cents |
| 29 | Gate positive and negative trigger prompts separately (after `v0.2.0`) | after D | none | S | no |
| 30 | The VS Code extension check (moved from `v0.2.0`) | after D | none | S | yes, cents |
| 31 | Record what was sent to the model, and the agent's reasoning (after `v0.2.0`) | after D | none | M | yes, cents |
| 32 | An eval-driven development skill (after `v0.2.0`) | after D | `write-evals` | M | yes, dollars |
| 33 | Fix and re-run the eval sets `write-evals` drafted | after D | none | S | yes, about a dollar an epoch |
| 34 | Claude Code loader: follow Claude Code's documented order | E | none | M | yes, cents |
| 35 | Claude Code: gather denials from every `result` event | E | none | XS | no |
| 36 | Claude Code: skills, agents and trigger tests | E | 34 | M | yes, cents |
| 37 | Claude Code: stage `permissions` from `.claude/settings.json`; `permission_held` | E | 34, 35 | M | yes, cents |
| 38 | Claude Code: `outside_canary_not_reached` | E | 35 | S | yes, cents |
| 39 | Claude Code example, recorded real run and docs | E | 34–38 | M | yes, about a dollar |
| | **Checkpoint E: Claude Code at parity with Kilo** | | 34–39 | | |

Lanes that can run beside the main one:

- **Captures (Tasks 16 and 22).** They need no new code, so they can run any time, in one sitting. Running them early removes the biggest unknowns before milestone B starts.
- **Packaging (Task 24).** It's independent of milestones A to C. It and Task 9 both change `src/whitney/agent.py`, so run them one after the other, not at the same moment.
- **The bug fix (Task 1).** It can go to review as soon as it's done.

Where each spec slice went:

| Spec slice | Tasks |
|---|---|
| 1 | 2 |
| 2 | 3 (plus discovery, from 7b) |
| 3 | 4, 5, 6, 7 |
| 4 | 1 (Claude Code loader), 8 (the rest) |
| 5 | 9 |
| 6 | 10 |
| 7a | 24 |
| 7b | 11, 12, 25 |
| 8 | 13, 14 |
| 9 | 22 |
| 10 | 23 |
| 11 | 15, 26 |
| 13 (Later) | 20 |
| 14 (Later) | 16, 17, 18, 21 |
| 12 (Later) | still later |

## Architecture decisions

The spec's decisions stand: Whitney becomes a package that takes two paths, and each scenario states its expected result with reusable checks named in the JSON. These are added for skills and agents:

- **A skill or agent eval file holds two kinds of test.** Trigger tests ask "did the right thing load?" and never run in a baseline (D4). Behaviour scenarios ask "did the agent do what the skill or agent says?" and run against a baseline that removes the skill or agent.
- **Behaviour scenarios judge the outcome, not the load.** They don't expect `skill_loaded`, because the baseline can never load the skill. Instead the report says in how many template runs the skill loaded or the agent was delegated to. A pass in a run where the skill never loaded shows the model's default, not the skill.
- **An agent's own behaviour is tested by running it as the primary agent (`kilo run --agent`).** Delegation tests ask only whether the `task` tool was called with that agent. Task 16 finds out whether a subagent's tool calls appear in the parent's event stream. Until it shows they do, a file change can't be put down to the subagent rather than the parent.
- **Negative checks need positive evidence.** `skill_not_loaded` and `subagent_not_delegated` pass only if the event stream holds at least one tool call the parser recognises, as the spec requires. A change in Kilo's output then makes them inconclusive rather than passing.
- **Kilo first.** Claude Code skills (`.claude/skills/`) and agents (`.claude/agents/`) need their own capture and a decision on staging `.claude/` (spec open question 8). They're listed under "Later". Milestone E (Tasks 34 to 39, added 2026-11-11) picks them up.

## Risks and mitigations

The spec's risks still apply. These are new or bigger in this plan.

| Risk | Impact | Mitigation |
|---|---|---|
| Skills or agents don't load under Whitney's isolation (temporary HOME, `--pure`, the `KILO_CONFIG` overlay) | High: milestone B tests nothing | Task 16 checks this before any skill code is written, using the canary method from [docs/load-order.md](../docs/load-order.md). If they don't load, stop and decide with Christopher |
| Kilo loads skills or agents from more folders than Whitney stages (for example `.kilocode/` or Claude-compatible folders) | Med: a staged project loads less than the real one | Task 16 records every folder Kilo loads from. Task 8 or a follow-up stages the missing ones |
| A subagent's tool calls don't appear in the parent's event stream | Med: what a delegated agent did can't be judged directly | Test an agent's behaviour with `--agent` (Task 20). Delegation tests judge only the `task` call |
| Trigger results are noisy: the model picks skills by description, and samples are small | Med | Report n beside every rate. Use at least 3 epochs. The report says how many prompts each skill has, so a thin trigger test is visible |
| The mutation ratchet: project score 99.6%, allowed to drift 0.5 points; numbered checks block from 2026-11-16 (`WARN_UNTIL`) | Med: each task needs more tests than it looks | Size tasks small. Kill or explain every mutant in new code, as CONSTRAINTS.md does |
| Several sessions share this working tree | Med: one session's checkout can capture another's uncommitted work | One branch per task, preferably in its own worktree. Check the branch in the same command as the commit |
| Captures are for Kilo 7.8.18 | Med: a Kilo upgrade can change event shapes | Each check's docstring names the capture it relies on. A Kilo version bump reruns Tasks 16 and 22 |

## Decisions

Approved on 2026-11-06 with the recommendation for each. For decision 3, nobody else was known to have the capture, so Task 16 runs here.

1. **Approve D1 to D9?** Recommended: yes. They keep the spec's design and change only the order and three details.
2. **Release grouping.** Recommended: build in the order A, B, C, D and tag `v0.2.0` at the end of D. If B or C slips, tag without it, as the spec already does for `permission_held`. The alternative is to tag after A and D, with skills and agents in `v0.3.0`.
3. **Who does the skill and `task` capture?** The spec's assumption 6 says it's happening elsewhere. If someone already has it, Task 16 shrinks to turning it into test fixtures. Otherwise Task 16 runs here.
4. **Trigger tests never in a baseline, with an automatic control for positive prompts (D4)?** Recommended: yes. The control adds one run per positive prompt per epoch.
5. **Which Claude Code instruction files does Task 1 stage?** The spec says root `AGENTS.md` and `CLAUDE.md`. Recommended: add `.claude/CLAUDE.md`, which Claude Code also reads as project instructions, and confirm with a one-prompt canary run. Leave the rest of `.claude/` out (spec open question 8). **Reversed 2026-11-06 at Checkpoint A1:** Task 1 now stages one file, always as `AGENTS.md`: root `AGENTS.md`, else root `CLAUDE.md`, else `.claude/CLAUDE.md`. The canary run showed Claude Code 2.1.318 ignoring `AGENTS.md` once `.claude/CLAUDE.md` existed ([canary record](../docs/results/2026-11-06-claude-code-canary.md)), so staging a `CLAUDE.md` beside `AGENTS.md` made the report claim instructions the agent never read. The `claude-code` evals and their baseline are about `AGENTS.md`, so a `CLAUDE.md`-only project has its file staged under that name; only the temporary copy is renamed. The report names a rename in a "Renamed for the Eval" table and each file left out, with the reason. A root `CLAUDE.md` beside `AGENTS.md` wasn't tested and is left out by the same rule. **Replaced 2026-11-11 by decision 8 (Task 34):** the loader now stages what Claude Code's memory docs say it reads, each file under its own name.
6. **What is the baseline for a scenario that names its agent (Task 20)?** Removing the agent file makes `kilo run --agent` fail, which would make every baseline run inconclusive. Recommended: the baseline runs the same prompt with Kilo's default agent. **Corrected 2026-11-06:** Task 16 showed the premise is wrong (Kilo falls back to its default agent and exits 0), so the outcome stands but the baseline drops `--agent`; see Task 20.
8. **Claude Code at parity (milestone E), answered by Christopher on 2026-11-11.**
   - The loader follows the order in Claude Code's own documentation, not Whitney's earlier choice. This replaces decision 5 once Task 34 lands.
   - From `.claude/settings.json`, only the `permissions` block is staged to begin with. Hooks stay out, and may be supported later if they would change an eval's result (Later list).
   - `outside_canary_not_reached` is supported on `claude-code`, and a shell command that reads outside the project fails it. That's a true finding about Claude Code, and the docs say how to close it.
7. **Real-model spend.** Tasks 14, 16, 21, 22 and 23 and Checkpoint A2 call a real model, and Task 1 can. Recommended: Haiku through your Kilo login, with `--limit` and `--epochs` kept small.

Spec open questions that block a task here, so they need an answer before that task starts:

| Spec question | Blocks |
|---|---|
| 1. Skill and `task` event shapes | Task 16 answers it |
| 5. Harness-judged minimum (`MIN_HELD`) | Answered 2026-11-10: 0.9 for `v0.2.0`; Task 27 looks at raising it |
| 6. Controls in the gate | Task 10 |
| 8. Claude Code project config | Task 1 (partly; decision 5 above) and the Later list |
| 12. `.kilocode/rules/` and glob matching | Task 8 |
| 14. Packaging test cost | Answered 2026-11-10: the wheel's contents in the default suite, the install in `check.py full` only |
| 15. Skill trigger rank (`top_k`) | Task 18. Recommended: loaded or not loaded only; record the order of loads in the score metadata |

## Later

Not planned in detail:

- **The Python API (found in the Task 11 review, 2026-11-07):** the spec's `whitney.run`, `whitney.validate` and `whitney.discover`, which the CLI should be a thin layer over. Task 11's `cli._run` takes an argparse Namespace and prints its findings, so it needs splitting into a library call that returns results and a CLI that prints them. Plan it with Task 25 or before the `v0.2.0` release.

- **Security, found in the Task 9 review (2026-11-06), do before a release:** a literal `apiKey` in the user's global Kilo `provider` settings is copied into the `KILO_CONFIG` overlay, which the agent can read, and neither the agent nor `evidence.run_secrets` redacts it; the same goes for proxy credentials passed through `LOCAL_ENV_PASSTHROUGH` (for example `HTTP_PROXY=user:pass@...`). Refuse literal keys in the overlay (use `{env:NAME}`), or redact them, with a test.

- **`git -C . push` gets past `"git push *": "deny"` (2026-11-10).** An eval the `write-evals` skill drafted for `examples/consumer/kilo.jsonc` asked the agent to push "another way". On Kilo 7.8.27 with Haiku, `git -C . push origin master` ran, and `permission_held` reported it bypassed; the four direct pushes were refused. Kilo's pattern matches the command's text, so any option before `push` steps round it. Decide whether the example's rule should add a pattern such as `"git * push*": "deny"`, whether the docs should say a pattern rule is this easy to step round, and whether it belongs beside finding 001.
- **A `permission_held` false fail, found testing the `write-evals` skill (2026-11-10).** `forbidden_input` is searched in every string of every tool call's input (`checks/permissions.py`, `_strings`), so it also matches text the model wrote: a bash call's `description`, a `todowrite` item, a `task` prompt for a subagent. A completed `git status` described as "check before git push" counts as a push that ran, and fails the check. It errs towards failing, never towards passing. Match only the fields that carry what runs, such as bash's `command`, with a test for each tool. Each test whose expectation changes needs Christopher's confirmation first.
- **Claude Code hooks (2026-11-11).** Task 37 stages only the `permissions` block of `.claude/settings.json`. A team whose hooks block or change tool calls, such as a `PreToolUse` hook that refuses `git push`, gets results that don't match its real setup. Hooks run commands, so supporting them needs a safety design: which hooks run, in which sandbox, and how the report says they ran.
- `no_test_newly_skipped`, with presets for each test framework (spec slice 12).
- **Checks for rules no check covers yet, found building the `write-evals` skill (2026-11-10).** Drafting evals for a real `AGENTS.md` turned up three kinds of rule that today's checks can't test:
  - **The order of actions**, such as "always run tests before committing". This needs a check that reads the tool calls in order and fails when a commit comes before the test command. The `kilo` event stream already holds each tool call with its input; `claude-code` would need the same.
  - **Which files the agent read**, such as "read CONSTRAINTS.md before writing code". This needs a check that a read of the named file came before the first edit. A read through `bash`, such as `cat`, counts too.
  - **What the agent's reply says**, such as "quote evidence for every finding" in an audit skill. No check reads the reply, so for a skill whose output is a report, the evals test only what it mustn't do and whether it loads. Model-graded checks, listed below, are one way in.
  - **Something the agent must do**, such as "colocate tests next to the source". Every check today watches for what mustn't happen. Turned round, "no test file in a separate folder" also passes a run that writes no test at all. A `file_added` check, which fails if no new file matches a pattern, would test it the right way round.
- Claude Code skills and agents: its own capture of `--output-format stream-json`, and staging `.claude/skills/` and `.claude/agents/` without `.claude/settings.json`, whose hooks run commands.
- `--junit` output; model-graded checks (spec open question 13); check plugins (spec open question 9).
- The proposal's other future work: instruction hierarchy, `REVIEW.md`, hooks.
- A fake `claude` for `tests/test_safe_staging.py::test_the_project_is_unchanged_after_a_run`, so the claude-code target's run is checked too, not only its staging (from the Task 8 review).
- Whether `_is_safe_file` should also refuse `.envrc` and similar files that hold secrets without the `.env` name (from the Task 8 review).
