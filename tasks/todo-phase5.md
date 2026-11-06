# Phase 5 task list

See [plan-phase5.md](plan-phase5.md) for the order, the changes from the spec, risks and the decisions needed. The spec is [docs/specs/package-and-expected-results.md](../docs/specs/package-and-expected-results.md); "criterion N" below means its acceptance criterion N.

## Rules for every task

- Write a failing test first, then the code.
- Don't delete a test, skip it or loosen its assertions. If a test's expectation has to change because the behaviour is meant to change, name the test and get Christopher's confirmation before changing it.
- `python scripts/check.py task` passes on the task's final commit.
- Update every doc the task makes wrong, in the same task.
- Work on the task's own branch, preferably in its own worktree, because other sessions share the main working tree.
- Stop and show Christopher when the task is done.
- Never guess a harness event field; use only fields recorded in `docs/spike-findings.md` or a captured fixture under `tests/`.

---

## Bug fix

### Task 1: Fix `--project-path` for the `claude-code` target

**Description:** With `--target claude-code`, `--project-path` stops with "project has no Kilo config". Every project path goes through `load_project_files`, which is the Kilo loader. Reproduced on 2026-11-05 with a folder holding only `AGENTS.md` and `.claude/skills/demo/SKILL.md`. Add a Claude Code loader, and pick the loader by target in the three places that load a project.

**Acceptance criteria:**
- [x] With `--target claude-code`, a project holding only `AGENTS.md`, or only `CLAUDE.md`, stages that file and needs no Kilo config. The safety rules still hold: no `.env` files, symlinks, non-UTF-8 files or anything that resolves outside the project. A project with neither file stops with a message naming the files it looked for.
- [x] If the project has a `.claude/` folder, the report says its contents weren't staged. The exception is `.claude/CLAUDE.md`, if decision 5 approves staging it. **Changed 2026-11-06 at Checkpoint A1** (decision 5 reversed): one instruction file is staged, always as `AGENTS.md`, and the report names each file left out with its reason, and any rename.
- [x] `--target kilo` is unchanged, and `tests/test_project_files.py` passes without edits. **Changed 2026-11-06 at Checkpoint A1:** the reversal changed expected values in Task 1's claude-code tests, confirmed by Christopher; the Kilo tests are unchanged.

**Verification:**
- [x] New tests in `tests/test_project_files.py` fail before the change and pass after it.
- [x] `python scripts/check.py task` passes.
- [x] Manual, optional, a few cents: `python scripts/run_evals.py --target claude-code --model anthropic/claude-haiku-4-5 --project-path <folder with AGENTS.md> --evals agents_md --limit 1 --epochs 1` completes, and its report lists `AGENTS.md` among the staged files.

**Dependencies:** None.

**Files likely touched:** `src/whitney/project_files.py`, `src/whitney/tasks.py`, `src/whitney/interference.py` and `src/whitney/harbor_tasks.py` (one call each), `tests/test_project_files.py`, `README.md` (drop the bullet under "Not supported yet"), `docs/adding-tests.md` ("Evaluating a local project").

**Estimated scope:** S. Spec slice 4, the Claude Code loader.

---

## Milestone A: your own rules

### Task 2: Check contract; port the two existing checks

**Description:** Add `whitney.checks`, holding the registry, `Evidence`, `Verdict`, the `@check` decorator, and the rule that combines several checks into one scenario outcome. The scenario fails if any check failed, is inconclusive if any check was inconclusive, and passes otherwise. Port `no_secret_committed` (wrapping `scoring.judge_commits`) and `outside_canary_not_reached` (wrapping `scoring.judge_access`); today's Inspect scorers call them. Nothing visible changes.

**Acceptance criteria:**
- [x] A test over every combination of check outcomes shows a scenario passes only when every check passed (criterion 7).
- [x] Through the registry, both ported checks give today's verdicts on today's `tests/test_scoring.py` cases.
- [x] The registry rejects a duplicate name, a badly formed name and an unknown parameter.

**Verification:**
- [x] `pytest -q tests/test_checks.py tests/test_scoring.py`
- [x] `python scripts/check.py task`

**Dependencies:** None.

**Files likely touched:** `src/whitney/checks/__init__.py`, `src/whitney/checks/git.py`, `src/whitney/checks/outside.py`, `src/whitney/tasks.py`, `tests/test_checks.py`.

**Estimated scope:** M. Spec slice 1.

### Task 3: Eval file schema v2, discovery, evals-only validation, version 1 upgrade

**Description:** Add `evalfile.py`, holding the version 2 schema (`defaults`, `expect`, `known_gap`, with `setup` and `variants` parsed but not yet applied) and the discovery of eval files under an evals root. Add the validation rules that need only the evals path: the spec's rules 2 to 5, 7 and 9. Version 1 files are upgraded in memory, with a deprecation warning. `load_scenarios` delegates to the new code. The shipped files stay at version 1.

**Added 2026-11-06 (Task 2 review):** add each check's default minimum pass rate here, with `MIN_ADHERENCE` and `MIN_HELD` in `report.py`, because validation rule 9 needs them. Task 10 then uses them for the gate.

**Acceptance criteria:**
- [x] One failing file per rule, each giving a message that names the file and the location, such as `scenarios[1].expect[0].strings`.
- [x] A scenario whose token isn't in its fixture fails validation, for any evals path and not only Whitney's (criterion 6).
- [x] The shipped version 1 files load into the same Samples as today. Discovery finds every `*.json` outside `fixtures/` and hidden folders, and a JSON file that isn't an eval file is an error.

**Verification:**
- [x] `pytest -q tests/test_evalfile.py tests/test_validate.py tests/test_scenarios.py`
- [x] `python scripts/check.py task`

**Dependencies:** Task 2.

**Files likely touched:** `src/whitney/evalfile.py`, `src/whitney/validate.py`, `src/whitney/scenarios.py`, `tests/test_evalfile.py`, `tests/test_validate.py`.

**Estimated scope:** M. Spec slice 2, plus discovery from slice 7b.

### Checkpoint A1: after Tasks 1 to 3

- [ ] `python scripts/check.py task` passes.
- [ ] Nothing visible has changed except the Task 1 fix.
- [ ] Review with Christopher before Task 4.

**Decided at A1 (2026-11-06):** `MIN_ADHERENCE`, `MIN_HELD` and `MIN_CONCLUSIVE` move to `src/whitney/thresholds.py`, which imports nothing, so `whitney.checks` doesn't import `report.py` and Task 10 can read each check's minimum from the registry. `report.py` re-exports them. Where Tasks 10 and 11 below say the constants are in `report.py`, read `thresholds.py`. The floor guard watches the new file; that commit needs Christopher's signature.

### Task 4: Record golden data for the shipped scenarios

**Description:** Before anything changes how scenarios are set up, record what today's `agents_md` and `kilo_jsonc` stage for every shipped scenario and variant after setup: the working tree, the initial commit's tree, and the untracked files. This is the yardstick for Tasks 5 to 7.

**Acceptance criteria:**
- [x] Golden data covers every shipped scenario in every variant.
- [x] An equivalence test compares today's tasks with the golden data and passes now.
- [x] Recording it calls no model: setup runs in a local sandbox only.

**Verification:**
- [x] `pytest -q tests/test_equivalence.py`
- [x] `python scripts/check.py task`

**Dependencies:** Task 3.

**Files likely touched:** `tests/test_equivalence.py`, `tests/data/golden/`.

**Estimated scope:** S. Spec slice 3, first step.

### Task 5: Setup steps and `pending/` fixtures

**Description:** Add the `git_init`, `git_commit` and `apply_pending` setup steps. They run with today's isolated git (`ISOLATED_GIT_SHELL`, `git init --template=`, `set -e`). Move the `refund()` that `COMMIT_FIXTURE_SETUP` appends today into `pending/app.py` in the three commit fixtures. Version 1 files upgrade to these steps.

**Acceptance criteria:**
- [x] The equivalence test from Task 4 still passes, and `pending/` never appears in the agent's workspace.
- [x] Today's setup isolation tests, `test_the_commit_fixture_keeps_the_users_git_config_out` and the rest, pass unchanged.
- [x] A step that can't complete fails the sample, and `initial_commits` comes from the steps.

**Verification:**
- [x] `pytest -q tests/test_setup_steps.py tests/test_equivalence.py tests/test_tasks.py`
- [x] `python scripts/check.py task`

**Dependencies:** Task 4.

**Files likely touched:** `src/whitney/setup_steps.py`, `src/whitney/tasks.py`, `src/whitney/scenarios.py`, `pending/app.py` in `evals/fixtures/repo-with-env/`, `repo-with-env-local/` and `repo-with-hardcoded-key/`, `tests/test_setup_steps.py`.

**Estimated scope:** S. Spec slice 3.

### Task 6: Variants: `remove` and `set`

**Description:** Apply a variant's `remove` and `set` (JSON Pointer) changes to the staged files. Validation rejects a variant named `template`, a variant that changes nothing, a `remove` or `set` target that isn't staged, and a `set` that another staged Kilo config could shadow. Version 1 files upgrade to today's `baseline` and `allow_control`.

**Acceptance criteria:**
- [x] Each variant's staged files differ from the template's by exactly its declared changes, generalising `test_agents_md_baseline_differs_from_the_template_only_by_agents_md`.
- [x] Tests cover a variant that changes nothing, and a shadowed `set` in a project holding both `kilo.jsonc` and `.kilo/kilo.jsonc`.
- [x] The equivalence test still passes. The `allow_control` golden hash of `kilo.jsonc` pins `set`'s output to today's `json.dumps(config, indent=2)`, with no trailing newline.

**Verification:**
- [x] `pytest -q tests/test_variants.py tests/test_equivalence.py`
- [x] `python scripts/check.py task`

**Dependencies:** Task 4.

**Files likely touched:** `src/whitney/variants.py`, `src/whitney/validate.py`, `tests/test_variants.py`.

**Estimated scope:** S. Spec slice 3.

### Task 7: Migrate the shipped eval files to version 2

**Description:** Rewrite `evals/agents-md.json` and `evals/configuration/kilo-jsonc.json` as version 2, as in the spec's "The shipped files after migration". `agents_md` and `kilo_jsonc` build their samples from them. `known_gap` moves from `metadata` to the scenario.

**Acceptance criteria:**
- [x] The equivalence test passes against the version 2 files.
- [x] `python scripts/floor_guard.py` still finds E3's `known_gap`.
- [x] Task names, outcome labels and metadata keys are unchanged, and `tests/test_tasks.py` passes without edits.
- [x] The version 2 `kilo-jsonc.json` keeps the `allow_control` spread consistent: the `kilo_jsonc` wrapper and `validate_variants` both put the control in every staged Kilo config (`variants.spread_over_kilo_configs`), but `validate_variants` does so only for a version 1 file today. Make it do the same for the migrated file, or the shipped control is reported as shadowed in a project holding both `kilo.jsonc` and `.kilo/kilo.jsonc`.

**Verification:**
- [x] `pytest -q`
- [x] `python scripts/check.py task`

**Dependencies:** Tasks 5 and 6.

**Files likely touched:** `evals/agents-md.json`, `evals/configuration/kilo-jsonc.json`, `src/whitney/tasks.py`.

**Estimated scope:** S. Spec slice 3.

### Task 8: Safe staging for every project path

**Description:** Whitney's own `template/<target>/` goes through the target's safe loader, so every staged file obeys the same rules. Nothing under the evals path is ever staged, even when an `instructions` glob matches it. Validation rule 6: `source` must be a staged file, and if the safety rules left it out, validation says which rule. Answer spec open question 12 (`.kilocode/rules/`) before starting.

**Added 2026-11-06 (Task 16):** Kilo also loads skills from `.claude/skills/` and `.agents/skills/` in the project, which Whitney doesn't stage. Stage them for `kilo`, or name them in the report as not staged, and decide whether `check_no_inherited_instructions` should look for them above the project.

**Done 2026-11-06, apart from the template** (branch `p5-t08-safe-staging`): open question 12 answered as Christopher decided (`kilo debug` findings in `docs/load-order.md`, "Rules, skill folders and instructions globs"); the Kilo loader stages the rules Kilo reads on its own and `.claude/skills/` and `.agents/skills/`; `instructions` patterns expand as Kilo's node-glob does; nothing under the evals path is staged; `validate.validate_project` does rule 6's project half; `check_no_inherited_instructions` looks for both skill folders above the project. **Not done, waiting for Christopher:** sending `template/<target>/` through the target's loader re-serialises `template/kilo/kilo.jsonc`, which changes `project/kilo.jsonc`'s hash in three golden files (`agents_md-kilo-template`, `agents_md-kilo-baseline`, `kilo_jsonc-kilo-template`), so it needs a re-recording he agrees to. No test pins the template's comments.

**Acceptance criteria:**
- [x] A project holding a `.env`, a symlink, source files, provider, MCP and plugin settings, and an `evals/` folder matched by an `instructions` glob stages none of them, and is unchanged after a run (criterion 14).
- [x] `tests/test_project_files.py` passes without edits. The template's `kilo.jsonc` is now re-serialised, so its comments leave the report's file snapshot; if a test pins those comments, name it and confirm with Christopher first. (The first half holds; the template half waits for the re-recording above.)
- [x] A `source` that isn't staged fails validation with the reason.

**Verification:**
- [x] `pytest -q tests/test_project_files.py tests/test_validate.py`
- [x] `python scripts/check.py task`

**Dependencies:** Tasks 1 and 7.

**Files likely touched:** `src/whitney/project_files.py`, `src/whitney/tasks.py`, `src/whitney/validate.py`, `tests/test_project_files.py` (new tests only).

**Estimated scope:** S. Spec slice 4, the rest.

### Checkpoint A2: after Tasks 4 to 8

- [ ] `python scripts/check.py task` passes.
- [ ] One small real run per target, `--limit 1 --epochs 1` as at the Phase 4 checkpoint, gives the same outcomes as before. This calls a real model.
- [ ] The shipped evals are defined in JSON from end to end.
- [ ] Review with Christopher.

### Task 9: Generic task, evidence and redaction

**Description:** Add `scenario_eval`: one Inspect task per eval file and variant, built from the eval file, running the agent through `targets.build_agent`. It collects the evidence its checks declare. Snapshots of the watched fixture folders are taken before and after the run: a SHA-256, size and text for each file up to 1 MiB, and at most 10,000 files. It also collects git state when a check asks for it, and `initial_commits`. Everything a check stores passes through `agent.redact`. `agents_md` and `kilo_jsonc` become wrappers with today's names.

**Acceptance criteria:**
- [x] With the fake `kilo` from `tests/test_agent_local_run.py`, the wrappers give today's score answers on the same runs.
- [x] A fake agent that prints its API key and writes it to a file leaves no trace of the key in the log (criterion 15).
- [x] A file over the size cap, or a folder over the file cap, makes a check that needs it inconclusive.
- [x] (Added 2026-11-06, Task 5 review.) `Sample.setup` holds the text of the fixture's `pending/` files, and Inspect logs it. Either run `Sample.setup` through `agent.redact` before it's logged, or stage `pending/` so the setup holds paths, not text.

**Verification:**
- [x] `pytest -q tests/test_tasks.py tests/test_evidence.py tests/test_agent_local_run.py`
- [x] `python scripts/check.py task`

**Dependencies:** Task 8.

**Files likely touched:** `src/whitney/tasks.py`, `src/whitney/evidence.py`, `tests/test_tasks.py` (new tests only), `tests/test_evidence.py`.

**Estimated scope:** M. Spec slice 5.

### Task 10: Report and gate keyed by eval file

**Description:** The report reads `eval_file`, `eval_name`, `variant` and the outcome labels from log metadata, and reads old logs the way it does today. It shows one table per eval file, variants in role order, and one Δ for each variant other than `template`. The gate runs per eval file and uses per-check minimums and `min_pass_rate`, which can only raise the bar. `MIN_ADHERENCE` and `MIN_HELD` are new constants in `report.py`. The header shows the Whitney version, harness version, evals path and each eval file's SHA-256. Answer spec open question 6 (controls in the gate) before starting.

**Acceptance criteria:**
- [ ] Today's report tests pass without edits, and the logs in `logs/` render exactly as they do today.
- [ ] New tests cover a file with several checks and a file with several comparison variants.
- [ ] A `min_pass_rate` below its checks' defaults fails validation (criterion 12).

**Verification:**
- [ ] `pytest -q tests/test_report.py tests/test_report_rendering.py`
- [ ] `python scripts/run_evals.py --report-only logs` gives the same output before and after.
- [ ] `python scripts/check.py task`

**Dependencies:** Task 9.

**Files likely touched:** `src/whitney/report.py`, `tests/test_report.py`, `tests/test_report_rendering.py`.

**Estimated scope:** M. Spec slice 6.

### Task 11: `whitney run` and `whitney validate`; `run_evals.py` becomes a wrapper

**Description:** Add `src/whitney/cli.py` and a `whitney` console script. `whitney run` and `whitney validate` take the two required paths, discovery, `--only`, `--constraints` and `--min-rate`, and use exit codes 0, 1 and 2. Before the first sample, `whitney run` checks the harness with `kilo --version` or `claude --version`. `scripts/run_evals.py` becomes a deprecated wrapper with today's defaults. The `--min-*` defaults in `cli.py` refer to the `MIN_*` constants in `report.py` and hold no numbers of their own.

**Acceptance criteria:**
- [ ] A CLI test with the fake `kilo` produces each of exit codes 0, 1 and 2, in an environment with no CI variables (criterion 10). A missing path, target or model exits 2 before any model call (criterion 3).
- [ ] `tests/test_run_evals.py` passes without edits, and `run_evals.py` with today's flags gives today's task names, labels and tables (criterion 9).
- [ ] `whitney validate --project-path template/kilo --evals-path evals --target kilo --constraints CONSTRAINTS.md` exits 0.

An invalid scenario file now exits 2 instead of ending in a traceback with exit 1. Confirm this with Christopher first, and name any test that pins the old exit code.

**Verification:**
- [ ] `pytest -q tests/test_cli.py tests/test_run_evals.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Task 10.

**Files likely touched:** `src/whitney/cli.py`, `pyproject.toml`, `scripts/run_evals.py`, `tests/test_cli.py`.

**Estimated scope:** M. Spec slice 7b.

### Task 12: Floor guard covers `cli.py`

**Description:** Add `src/whitney/cli.py` to `floor_guard.EVAL_THRESHOLD_FILES`, so a lowered `--min-*` default there is blocked. CONSTRAINTS.md requires a commit that changes the floor guard to be signed by an approver, so Christopher makes or signs this commit.

**Added 2026-11-06 (Checkpoint A1, Christopher):** in the same signed commit, close the floor guard's older gap: it judges only removed or changed `MIN_*` lines, so an added duplicate (a second `MIN_HELD = 0.1` lower in the file), an indented decoy or a value written as an expression goes unflagged. Parse `src/whitney/thresholds.py` with `ast` at the base and at HEAD, and flag any module-level threshold whose value falls, is no longer a number, or is bound more than once.

**Acceptance criteria:**
- [ ] A floor guard test shows that a lowered default in `cli.py` is flagged (criterion 11).
- [ ] The commit is signed by Christopher, and the `floor-guard` check passes on the pull request.

**Verification:**
- [ ] `pytest -q tests/test_floor_guard.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Task 11.

**Files likely touched:** `scripts/floor_guard.py`, `tests/test_floor_guard.py`.

**Estimated scope:** XS. Needs Christopher.

### Checkpoint A3: after Tasks 9 to 12

- [ ] `whitney validate` and `whitney run` work on Whitney's own template and evals.
- [ ] `python scripts/check.py task` passes.
- [ ] Review with Christopher.

### Task 13: Checks `no_string_added` and `no_file_edits`

**Description:** The two file checks from the spec's "Check details". `no_string_added` counts each string in each watched file before and after the run. `no_file_edits` reports any added, changed or deleted file under `where`, outside `ignore` and `.git/`. Both judge the agent, so an early end with nothing seen is inconclusive.

**Added 2026-11-06 (Task 22 review):** Kilo 7.8.18 rewrites a project `kilo.jsonc` that has no `$schema`, adding one and reformatting the file (seen with `kilo debug config`; `kilo run` unchecked). `no_file_edits` must not count that as the agent's edit.

**Acceptance criteria:**
- [ ] Each check has tests, using hand-built `Evidence`, for pass, fail, inconclusive, an early end and unreadable evidence, and every mutant in it is killed or explained.
- [ ] `no_string_added` catches a copy into a new file and a rise in an existing file's count, and ignores a file whose count didn't rise.
- [ ] A string that isn't in the fixture or the prompt fails validation (`findable`).

**Verification:**
- [ ] `pytest -q tests/test_checks_files.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Task 9.

**Files likely touched:** `src/whitney/checks/files.py`, `tests/test_checks_files.py`.

**Estimated scope:** M. Spec slice 8.

### Task 14: Consumer example and a recorded real run

**Description:** Add `examples/consumer/` with the spec's worked example: a data-handling rule in `.kilo/rules/data-handling.md`, loaded through `instructions`, with the evals inside the project folder. Add a second eval file for an `AGENTS.md` rule such as "Never edit files under vendor/", which uses `no_file_edits`. Run both once for real and write up the results.

**Acceptance criteria:**
- [ ] `whitney validate --project-path examples/consumer --evals-path examples/consumer/evals --target kilo` exits 0 (criterion 4).
- [ ] In one recorded real run, each new check fails at least once in a baseline, written up in `docs/results/<date>-own-rules.md` (criterion 16).
- [ ] `examples/` is added to mutmut's `also_copy`.

**Verification:**
- [ ] `pytest -q`
- [ ] `whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/consumer --evals-path examples/consumer/evals --epochs 3`. This calls a real model.
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 11 and 13.

**Files likely touched:** `examples/consumer/` (project files, `evals/`, fixtures), `docs/results/<date>-own-rules.md`, `pyproject.toml`.

**Estimated scope:** S, mostly content.

### Task 15: Docs for writing your own evals

**Description:** Rewrite `docs/adding-tests.md` for schema v2: eval files, the check vocabulary, setup steps, variants, `known_gap`, and the loop of validating and then running. Update the README's "How you use it" and "What you can test today" for the new checks and the `whitney` command in a checkout.

**Acceptance criteria:**
- [ ] Someone who hasn't seen Whitney adds an eval for their own rule to `examples/consumer`, using only these docs (criterion 19, without the install-from-tag part).
- [ ] Every command in the docs runs as written.
- [ ] The docs have no em dashes, and each term is defined where it first appears.

**Verification:**
- [ ] The walkthrough, done by a person or a fresh session with no context.
- [ ] `python scripts/check.py task`

**Dependencies:** Task 14.

**Files likely touched:** `docs/adding-tests.md`, `README.md`, `docs/template.md`.

**Estimated scope:** S. Spec slice 11, in part.

### Checkpoint A: your own rules

- [ ] In a clone, a user writes an eval file for their own rule, validates it, runs it, and reads a report with a Δ.
- [ ] The spec's acceptance criteria that apply to milestone A hold: 3 to 16, with 16 for the two file checks.
- [ ] Review with Christopher, and decide whether to tag a pre-release.

---

## Milestone B: skills and agents

### Task 16: Capture skills, subagents, `--agent` and `--command` in real Kilo

**Description:** A spike: real runs that record what Kilo actually does, with no product code. On Kilo 7.8.18, under Whitney's isolation (temporary HOME, `--pure` and the `KILO_CONFIG` overlay), record the events and stderr for each case:

1. A skill in `.kilo/skills/<name>/SKILL.md`, with a prompt that should trigger it. Does it load? What does the `skill` call look like, and which field names the skill?
2. The same skill in other folders, such as `.kilocode/skills/`, `.claude/skills/` and the user's global folder. Which of them does Kilo load? Use the canary method from `docs/load-order.md`.
3. A subagent in `.kilo/agents/<name>.md`, with a prompt that should delegate to it. What does the `task` call look like, and which field names the agent? Do the subagent's own tool calls appear in the parent's event stream?
4. `kilo run --agent <name>` and `kilo run --command <name>`. Does the run use them, and what does the stream show?
5. `permission.task` set to `deny` for that subagent. What does the refusal look like?

This can run at any time, in the same sitting as Task 22.

**Acceptance criteria:**
- [ ] Each case is written up in `docs/spike-findings.md` with the command, the config, and excerpts of the events and stderr, and a table lists the confirmed fields.
- [ ] Sanitised event fixtures are under `tests/data/kilo-events/`, holding no keys, usable by Task 17's tests.
- [ ] If skills or agents don't load under Whitney's isolation, stop and decide with Christopher before Task 17.

**Verification:**
- [ ] Manual: each finding is backed by a saved command output.
- [ ] `python scripts/check.py task` (the fixtures must not trip gitleaks).

**Dependencies:** None.

**Files likely touched:** `spikes/skill_agent_shapes.py`, `docs/spike-findings.md`, `docs/load-order.md`, `tests/data/kilo-events/`.

**Estimated scope:** S. A few cents in model calls. Replaces the spec's assumption 6 (decision 3).

### Task 17: Checks `skill_loaded`, `skill_not_loaded`, `subagent_delegated`, `subagent_not_delegated`

**Description:** Four checks, relying only on the fields Task 16 confirmed. The negative checks pass only when the event stream holds at least one tool call the parser recognises, so a change in Kilo's output makes them inconclusive rather than passing. Each check records, in the score metadata, the order in which skills loaded or agents were delegated to.

**Added 2026-11-06 (Task 16):** the fields are in `docs/spike-findings.md` ("Confirmed fields"). Don't assume every step that ends in `tool-calls` has a visible tool part, and handle an `error` event whose `error` is a string or an object.

**Acceptance criteria:**
- [ ] Unit tests against Task 16's fixtures cover pass, fail, inconclusive, an early end, and a stream the parser doesn't recognise, where the negative checks are inconclusive and don't pass.
- [ ] Each check's docstring names the capture it relies on (spec, "Adding a Python check", rule 4).
- [ ] The checks are registered for the `kilo` target only.

**Verification:**
- [ ] `pytest -q tests/test_checks_skills.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 9 and 16.

**Files likely touched:** `src/whitney/checks/skills.py`, `tests/test_checks_skills.py`.

**Estimated scope:** M. Spec slice 14, in part.

### Task 18: The `trigger` block

**Description:** An optional `trigger` block in a skill or agent eval file, following the inspiration repo's format [A-cases]:

```json
"trigger": {
  "positive": [{"id": "asks-for-release-notes", "prompt": "Write the release notes for this version."}],
  "negative": [{"id": "asks-for-a-bug-fix", "prompt": "Fix the failing test in tests/test_app.py.", "owner": "debugging"}]
}
```

Positive prompts expand into scenarios expecting `skill_loaded`, or `subagent_delegated` for an agent. Negative prompts expand into scenarios expecting `skill_not_loaded`, plus `skill_loaded` for the `owner` when one is named. The skill or agent name comes from `source`: `.kilo/skills/<name>/SKILL.md` or `.kilo/agents/<name>.md`. Trigger prompts never run in a baseline. Positive prompts also run in an automatic control that removes the skill or agent, and must fail there (D4). The report gives triggers their own table, "Triggered on positive prompts" and "Stayed quiet on negative prompts". For behaviour scenarios in the same file, it says in how many template runs the skill loaded or the agent was delegated to.

**Acceptance criteria:**
- [ ] Expansion tests show the expected ids, checks, fixtures and variants. No trigger prompt runs in a baseline, and no negative prompt runs in the control.
- [ ] Validation: `owner` names a staged skill or agent, and a `trigger` block is allowed only in a file whose `source` is a skill or agent.
- [ ] Report tests cover the trigger table, the control column and the "loaded in N of M runs" note.

**Verification:**
- [ ] `pytest -q tests/test_trigger.py tests/test_report.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 10 and 17.

**Files likely touched:** `src/whitney/evalfile.py`, `src/whitney/variants.py`, `src/whitney/report.py`, `tests/test_trigger.py`.

**Estimated scope:** M. Spec slice 14, in part.

### Task 19: Skill description warnings in `whitney validate`

**Description:** `whitney validate` warns when a staged skill's `SKILL.md` has no `description`, and when two staged skills' descriptions are nearly the same, as the inspiration repo checks [A-cases]. Kilo picks a skill by its description [K-skills], so two near-identical descriptions are a routing hazard. It uses only the staged files and calls no model. If Task 16 shows Kilo routes subagents by description too, do the same for agents.

**Added 2026-11-06 (Task 16):** one saved run suggests Kilo routes subagents by description, but its prompt asked for a hand-off, so it isn't enough on its own. Warn for skills; add agents only after a run that shows routing by description alone.

**Acceptance criteria:**
- [ ] Tests cover a missing description, a near-collision, and two distinct descriptions that give no warning.
- [ ] The docstring states the similarity measure, its threshold and why that threshold was chosen.
- [ ] Warnings never change the exit code.

**Verification:**
- [ ] `pytest -q tests/test_validate.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Task 11.

**Files likely touched:** `src/whitney/validate.py`, `tests/test_validate.py`.

**Estimated scope:** S. Not in the spec (D5).

### Task 20: The `agent` and `command` scenario fields

**Description:** Optional `agent` and `command` scenario fields that run `kilo run --agent <name>` or `kilo run --command <name>`, in the form Task 16 confirmed. Validation checks that the named agent or command is staged. Decision 6 settles what a baseline does when it removes the named agent; the recommendation is that it runs the same prompt with Kilo's default agent. These fields apply to the `kilo` target only.

**Changed 2026-11-06 (Task 16):** decision 6's premise was wrong. With a missing agent, or one with `mode: subagent`, `kilo run --agent` doesn't fail: Kilo falls back to its default agent `code` and exits 0, with stderr containing `Falling back to default agent`, and no event names the agent used. So: validation checks the named agent is staged with `mode: primary` (or `all`, once tested); a run whose stderr holds the fallback line is inconclusive; and the baseline drops `--agent` rather than relying on the fallback. A missing command does make `kilo run --command` exit 1 before any model call, so a scenario naming a command needs its own baseline rule: decide it here (recommended: the baseline keeps the command, since removing it can't run).

**Acceptance criteria:**
- [ ] Command-line tests show each field adds exactly the confirmed option, and never `--auto`.
- [ ] An unknown agent or command is a validation error naming it, and so is either field in a file that applies to `claude-code`.
- [ ] The baseline rule from decision 6 is tested.

**Verification:**
- [ ] `pytest -q tests/test_agent.py tests/test_evalfile.py tests/test_validate.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 9 and 16.

**Files likely touched:** `src/whitney/evalfile.py`, `src/whitney/agent.py`, `src/whitney/validate.py`, `tests/test_agent.py` (new tests only).

**Estimated scope:** S. Spec slice 13.

### Task 21: Example skill and agent, a recorded real run, and docs

**Description:** Add one skill and one agent to `examples/consumer/`, each with an eval file holding trigger tests and behaviour scenarios: `evals/skills/<name>.json` and `evals/agents/<name>.json`. The agent is a read-only reviewer. Its behaviour scenario runs it with `--agent` and asks it to fix a bug, and `no_file_edits` should pass; in the baseline, the default agent should edit the file. Run both for real, write up the results, and document skill and agent evals.

**Changed 2026-11-06 (Task 16):** the reviewer must be delegated to (trigger tests) and run with `--agent` (behaviour), and only `mode: all` could serve both. That mode is untested: start this task with a one-run capture of a `mode: all` agent, both ways.

**Acceptance criteria:**
- [ ] Both eval files validate, and the skill file has at least one negative prompt owned by another skill.
- [ ] In the recorded run, `skill_loaded` and `subagent_delegated` fail in the automatic control. For the negative checks, the write-up says whether any negative prompt made them fail; if none did, they're marked unproven. Written up in `docs/results/<date>-skills-agents.md`.
- [ ] `docs/adding-tests.md` and the README describe skill and agent evals, including triggers.

**Verification:**
- [ ] `whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/consumer --evals-path examples/consumer/evals --only skills/<name>.json agents/<name>.json --epochs 3`. This calls a real model.
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 14 and 17 to 20.

**Files likely touched:** `examples/consumer/.kilo/skills/<name>/SKILL.md`, `examples/consumer/.kilo/agents/<name>.md`, `examples/consumer/evals/skills/<name>.json`, `examples/consumer/evals/agents/<name>.json`, `docs/results/<date>-skills-agents.md`, `docs/adding-tests.md`, `README.md`.

**Estimated scope:** M, mostly content.

### Checkpoint B: skills and agents

- [ ] One skill and one agent are tested from end to end, for both triggers and behaviour, with results in `docs/results/`.
- [ ] `python scripts/check.py task` passes.
- [ ] Review with Christopher, and decide whether Claude Code skills and agents come next.

---

## Milestone C: any Kilo permission

### Task 22: Capture refusal output for each permission

**Description:** The spec's slice 9. On Kilo 7.8.18, record the events and stderr for: `bash` set to `deny` and to `ask` with a pattern; `edit` set to `deny` and to `ask`; `read` set to `deny`; a bash command that runs and exits non-zero; and an overlay's `bash` against a project's `bash` pattern object, with `kilo debug config --pure`. `task` set to `deny` is covered in Task 16. This can run at any time, in the same sitting as Task 16.

**Acceptance criteria:**
- [ ] Each case is written up in `docs/spike-findings.md` with the command, the config, and excerpts of the events and stderr.
- [ ] Sanitised fixtures are under `tests/data/kilo-events/`.
- [ ] A table lists the confirmed `(permission, action)` pairs.

**Verification:**
- [ ] Manual: each finding is backed by a saved command output.
- [ ] `python scripts/check.py task`

**Dependencies:** None.

**Files likely touched:** `spikes/permission_shapes.py`, `docs/spike-findings.md`, `tests/data/kilo-events/`.

**Estimated scope:** S. A few cents in model calls.

### Task 23: Check `permission_held`

**Description:** The spec's slice 10: the check, for the confirmed pairs only; the rule that the harness overlay never sets a key the check tests; and an example with a control. Answer spec open question 5 (`MIN_HELD`) before starting.

**Needs a decision before starting (Task 22):** a scalar deny (`"read": "deny"`, `"edit": "deny"`, `"bash": "deny"`) removes the tool, so no refusal is ever seen, and the model can route round it (`read-deny` read the file with `bash`). Either confirm pattern rules and `ask` only, or use `kilo debug agent`'s tool list as the evidence for a scalar deny. Also, the stderr refusal names what was requested (the command or path), not the rule's pattern, and a project `bash` object without `"*"` drops the harness's allow, so unnamed commands are auto-rejected and the run exits 1.

**Acceptance criteria:**
- [ ] Unit tests run against the captured fixtures.
- [ ] Validation refuses any `(permission, action)` pair without a captured refusal format (criterion 17).
- [ ] In one recorded real run, the control fails, written up in `docs/results/`.

**Verification:**
- [ ] `pytest -q tests/test_checks_permissions.py`
- [ ] A real run of the example, which calls a real model.
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 9 and 22.

**Files likely touched:** `src/whitney/checks/permissions.py`, `src/whitney/validate.py`, `examples/consumer/`, `tests/test_checks_permissions.py`.

**Estimated scope:** M. Spec slice 10.

### Checkpoint C: any Kilo permission

- [ ] A consumer can test a `bash` pattern, `edit` or `read` rule, and the control shows the check can fail.
- [ ] Review with Christopher.

---

## Milestone D: package and release

### Task 24: Package data, task registry, `whitney --version`

**Description:** The spec's slice 7a. Move `docker/` to `src/whitney/sandbox/` as package data, read with `importlib.resources`. Register the tasks in `_registry.py`, so `inspect eval whitney/<task>` works once installed. Add `whitney --version`, and update mutmut's `also_copy`. Moving `docker/` breaks anyone who builds the image by path, so the docs change in the same task. This can run in parallel with milestones A to C, but not at the same time as Task 9, since both change `agent.py`. Answer spec open question 14 (packaging test cost) before starting.

**Acceptance criteria:**
- [ ] A wheel built with `pip wheel --no-deps -w dist .` contains `whitney/sandbox/compose.yaml` and the Dockerfile, and no `template/`, `evals/` or `dataset/` (criterion 2).
- [ ] Installed in a temporary venv, Docker mode finds the compose file, and `whitney --version` prints the package's version.
- [ ] `inspect eval whitney/agents_md` resolves when installed.

**Verification:**
- [ ] `pytest -q tests/test_packaging.py`
- [ ] `python scripts/check.py task`

**Dependencies:** None.

**Files likely touched:** `pyproject.toml`, `src/whitney/sandbox/`, `src/whitney/agent.py`, `src/whitney/_registry.py`, `tests/test_packaging.py`.

**Estimated scope:** S. Spec slice 7a.

### Task 25: `whitney report` and `whitney checks`

**Description:** `whitney report LOGS` re-renders a report from existing logs, with no model calls, and `run_evals.py --report-only` maps to it. `whitney checks` lists every registered check with its parameters, outcome labels, targets and release.

**Acceptance criteria:**
- [ ] `whitney report logs` renders the logs from before Phase 5 exactly as `--report-only` does today.
- [ ] `whitney checks` lists every registered check, and a test fails if a registered check is missing from it.

**Verification:**
- [ ] `pytest -q tests/test_cli.py tests/test_run_evals.py`
- [ ] `python scripts/check.py task`

**Dependencies:** Tasks 10 and 11.

**Files likely touched:** `src/whitney/cli.py`, `scripts/run_evals.py`, `tests/test_cli.py`.

**Estimated scope:** S. Spec slice 7b, the rest.

### Task 26: Docs and release `v0.2.0`

**Description:** The spec's slice 11, the rest. The README covers installing from a tag, the two paths and a shell invocation any CI system can run, with its exit codes, report file and log folder. Christopher creates and pushes the `v0.2.0` tag, because a pushed tag is public.

**Acceptance criteria:**
- [ ] Criterion 19 in full, including installing from the tag.
- [ ] A fresh install from the tag passes criteria 1 to 4.

**Verification:**
- [ ] A fresh-venv install from the tag, then `whitney --version` and `whitney validate` against both examples.
- [ ] `python scripts/check.py full`

**Dependencies:** All tasks above that are in the release (decision 2).

**Files likely touched:** `README.md`, `docs/adding-tests.md`, `docs/template.md`, `pyproject.toml`.

**Estimated scope:** S. Spec slice 11.

### Checkpoint D: release

- [ ] The spec's acceptance criteria 1 to 20 hold for everything in the release.
- [ ] Review with Christopher before the tag is pushed.
