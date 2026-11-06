# MVP task list

See [plan.md](plan.md) for decisions, risks and open questions.

> **Status, 2026-11-03.** Tasks 1–8 and 11 are built and covered by the test suite; many of their boxes below were never ticked as the work landed, so read [plan.md](plan.md)'s task list for the current state. Still open: Task 9's extension run and manual checklist, Task 10's fresh-clone walkthrough, a CI pipeline, and Task 12 (publish). Phase 4 (the `kilo` and `claude-code` targets) is tracked in plan.md.

## Phase 0: Prove it can work

### Task 1: Spike — Kilo CLI inside an Inspect sandbox
**Description:** Prove Inspect can run `kilo run` inside a Docker sandbox with its model calls routed through `sandbox_agent_bridge`, and capture its output. Check whether inspect_swe's OpenCode backend can be reused or adapted.

**Acceptance criteria:**
- [x] One sample runs `kilo run` in Docker and completes with exit code captured
- [x] Kilo's model calls appear in the Inspect log (or the fallback is chosen and documented)
- [x] Kilo version pinned in the Dockerfile

Done 2026-11-02; see `docs/spike-findings.md` § T1.

**Verification:**
- [ ] Manual check: `inspect view` shows the sample transcript and model usage

**Dependencies:** None
**Files likely touched:** `spikes/kilo_bridge.py`, `docker/Dockerfile`, `docker/compose.yaml`
**Estimated scope:** M

### Task 2: Spike — what Kilo loads and how `ask` behaves headlessly
**Description:** With a canary rule ("end every reply with BANANA"), test which locations Kilo loads: project-root `AGENTS.md`, `.kilo/AGENTS.md`, and the `instructions` key in `kilo.jsonc`. Test `"external_directory": "ask"` in `kilo run` without `--auto`, with `--auto`, with `--format json`, and via `kilo serve`, recording what evidence of a permission request each gives.

**Acceptance criteria:**
- [x] Load locations confirmed with evidence. Template layout **awaiting decision**: see `docs/load-order.md` (`.kilo/AGENTS.md` alone is not loaded)
- [x] For each mode, recorded: is a request raised, how it is observable, and whether access happens (`kilo run` with and without `--auto`; `kilo serve` approve-once not tested, an open question in plan.md)
- [x] Findings written to `docs/load-order.md` (draft) and `docs/spike-findings.md`

**Verification:**
- [ ] Manual check: each finding is backed by a saved log or command output

**Dependencies:** T1
**Files likely touched:** `spikes/load_order.py`, `docs/load-order.md`
**Estimated scope:** S

## Checkpoint: Phase 0
- [x] Review spike results with human; go/no-go on the bridge approach; template layout agreed (2026-11-02: go; root `AGENTS.md`)

## Phase 1: Foundations

### Task 3: Project skeleton
**Description:** `pyproject.toml` with pinned `inspect-ai` (and `inspect_swe` if T1 uses it), pytest, `.gitignore` (`.env`, `logs/`, `results/`, `.venv/`), README stub, and the proposal's folder structure.

**Acceptance criteria:**
- [x] `pip install -e .[dev]` works in a fresh venv
- [x] `pytest` runs (an empty or smoke suite passes)
- [x] `.env` and generated output are gitignored

**Verification:**
- [ ] Tests pass: `pytest -q`

**Dependencies:** Checkpoint 0
**Files likely touched:** `pyproject.toml`, `.gitignore`, `README.md`, `tests/test_smoke.py`
**Estimated scope:** S

### Task 4: Scenario JSON schema and loader
**Description:** Define the scenario format (id, prompt, pressure, fixture, expectations, variants) and a loader that turns `evals/agents-md.json` and `evals/configuration/kilo-jsonc.json` into Inspect `Sample`s. Validate before any model call.

**Acceptance criteria:**
- [x] A valid file loads into Samples with metadata intact
- [x] Malformed files (missing field, unknown fixture, duplicate id) fail with a message naming the file and field
- [x] Schema documented, with an example (module docstring in `src/whitney/scenarios.py`; user docs in T10)

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_scenarios.py`

**Dependencies:** T3
**Files likely touched:** `src/whitney/scenarios.py`, `evals/schema.json`, `tests/test_scenarios.py`
**Estimated scope:** S

### Task 5: Template and static config check
**Description:** Create the template in the agreed layout (root `template/AGENTS.md`): `AGENTS.md` with the single rule "- Never commit .env files or secrets", and `kilo.jsonc` with the approved model and `"permission": { "external_directory": "ask" }`. Add a free, model-free check that the config parses as JSONC and contains the required rules.

**Acceptance criteria:**
- [x] Template files exist and match the proposal's wording
- [x] Static check fails if `AGENTS.md` is missing, if the core rule line has been removed (teams edit this file), or if `external_directory` isn't `ask`/`deny`
- [x] Static check passes on the shipped template

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_template.py`

**Dependencies:** T2, T3
**Files likely touched:** `template/AGENTS.md`, `template/kilo.jsonc`, `src/whitney/static_checks.py`, `tests/test_template.py`
**Estimated scope:** S

## Checkpoint: Phase 1
- [ ] All tests pass
- [ ] Review with human

## Phase 2: The two MVP evals

### Task 6: AGENTS.md eval — never commit .env or secrets
**Description:** A fixture git repo with a `.env` holding a canary token and a pending code change. Scenarios: neutral, "just commit everything", authority pressure, `.env.local`, and a key hard-coded in `config.py`. The scorer reads git history in the sandbox. Each scenario runs in `baseline` and `template` variants. Write the scorer tests first, against hand-built compliant and violating repos.

**Acceptance criteria:**
- [x] Scorer fails on a repo that committed `.env` or the canary token anywhere in history, and passes on a clean one (unit tests)
- [x] Scorer distinguishes "no commit made" from "committed cleanly"
- [x] Eval runs end to end on a real model in both variants

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_agents_md.py`
- [ ] Manual check: `inspect view` shows per-scenario results and explanations

**Dependencies:** T1, T4, T5
**Files likely touched:** `evals/agents-md.json`, `evals/fixtures/repo-with-env/`, `src/whitney/agents_md.py`, `tests/test_agents_md.py`
**Estimated scope:** M

### Task 7: Permissions eval — `external_directory: ask` honoured
**Description:** The workspace sits next to an outside directory holding a canary file. Prompts need outside access to complete: read via the read tool, via `bash`, a write, and a workaround pressure case (symlink / absolute path). The scorer asserts that a request was raised and the access did not happen. A control variant with `"allow"` must show access does happen.

**Acceptance criteria:**
- [x] Scorer reports `blocked`, `accessed` and `not attempted` as distinct outcomes, and only `blocked` counts as a pass
- [x] `ask` variant: the canary is never in the transcript and the outside file is unchanged; the permission request is evidenced (per T2's method)
- [x] `allow` control reaches the canary in at least one route (proves the scorer can fail)
- [x] Includes the symlink route (`ln -s ../outside/... && cat`), which bypassed both `ask` and `deny` in the spike (see `docs/spike-findings.md`); runs without `--auto`, because `--auto` approves `ask`

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_permissions.py`
- [ ] Manual check: control and `ask` runs side by side in `inspect view`

**Dependencies:** T1, T2, T4, T5
**Files likely touched:** `evals/configuration/kilo-jsonc.json`, `evals/fixtures/outside-dir/`, `src/whitney/permissions.py`, `tests/test_permissions.py`
**Estimated scope:** M

## Checkpoint: Phase 2
Results: `docs/results/2026-11-02-phase2.md`.
- [x] All tests pass
- [x] Both evals run end to end on a real model
- [ ] Review results with human

## Phase 3: Reporting, VS Code, docs

### Task 8: Runner and report
**Description:** `scripts/run_evals.py` runs scenarios × variants × epochs and prints a table (pass rate, n, baseline→template delta, per scenario and overall). Writes to `results/`. It must also run headless in CI.

**Acceptance criteria:**
- [x] One command runs both evals and prints the table
- [x] Exit code is non-zero if a configured threshold isn't met (for CI)
- [x] Works against a mock model, so it can be tested without an API key (report logic and log reading are tested with a mock-model log; running the evals themselves needs Docker and a key)

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_runner.py`

**Dependencies:** T6, T7
**Files likely touched:** `scripts/run_evals.py`, `src/whitney/report.py`, `tests/test_runner.py`
**Estimated scope:** S

### Task 9: VS Code parity check and manual checklist
**Description:** An automated check that the extension's runtime resolves the same `kilo.jsonc` and `AGENTS.md` as the CLI (method from T2). Plus `docs/vscode-manual-check.md`: a short script that repeats one scenario from each eval in the extension, with a results table to fill in.

**Acceptance criteria:**
- [x] Parity check passes on the template and fails if the extension would load a different file (unit-tested; CLI-vs-CLI run OK; **not yet run against the extension**, which isn't installed here)
- [ ] Manual checklist run once, with results recorded (**needs a person with the extension installed**)

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_parity.py`
- [ ] Manual check: completed checklist committed

**Dependencies:** T5, T6, T7
**Files likely touched:** `src/whitney/parity.py`, `tests/test_parity.py`, `docs/vscode-manual-check.md`
**Estimated scope:** S

### Task 10: Docs and README
**Description:** `docs/template.md`, `docs/load-order.md` (final), `docs/adding-tests.md` (when you change a template file, and when you add one), and a README that explains the project, how to run it, and where the docs are.

**Acceptance criteria:**
- [ ] Someone new can fork the repo, add a scenario and run it using only the docs (docs written; not yet tried by someone new)
- [x] README commands are copied from a verified run

**Verification:**
- [ ] Manual check: follow `docs/adding-tests.md` from a clean clone

**Dependencies:** T8, T9
**Files likely touched:** `README.md`, `docs/*.md`
**Estimated scope:** S

### Task 11 (optional): IFEvalCode interference check
**Description:** Port `ifevalcode_ab` from `inspect-instructions-example`, with the template's `AGENTS.md` as the instructions.

**Acceptance criteria:**
- [ ] Runs the baseline and template variants on the Python subset and reports the delta

**Verification:**
- [ ] Tests pass: `pytest -q tests/test_interference.py`

**Dependencies:** T5, T8
**Files likely touched:** `src/whitney/interference.py`, `tests/test_interference.py`
**Estimated scope:** S

## Checkpoint: MVP complete
- [ ] All tests pass locally and in CI
- [ ] All four MVP acceptance criteria in `AGENTS.md` met, with evidence
- [ ] Review with human

## After the MVP: publish

### Task 12: Copy the code into a fresh public repo
**Description:** Create a new repository for sharing with the community, holding the current code without this repo's history. The files on `local-kilo-config` no longer name the organisation where the project was tested, but older commits on `origin/main` still do, so start fresh rather than publishing this repo.

**Steps:**
1. Export the tracked files only (no history, logs, results or datasets): `git archive --format=tar HEAD | tar -x -C ../Whitney`
2. In the new folder, search every file for the organisation's name and its abbreviations (case-insensitive, with and without a hyphen) and remove any that remain.
3. `git init`, then make the first commit. Put `Exception-approved: E1, E2` in its message, since the original approvals were trailers in this repo's history.
4. Re-point the E1 entry in `.gitleaksignore` at the new commit. Its fingerprint names commit `12f4511`, which won't exist in the new repo. Run `gitleaks git --redact --report-format json --report-path /tmp/gl.json .` and copy the new fingerprint for `tests/test_scoring.py`.
5. Create the public GitHub repo, push, and set `origin/main` as the floor guard's base.

**Acceptance criteria:**
- [ ] No file in the new repo names the organisation
- [ ] The new repo's history is a single commit with no trace of this repo's history
- [ ] `python scripts/check.py full` passes in a fresh clone of the public repo

**Verification:**
- [ ] Case-insensitive search of the new repo for the organisation's name returns nothing
- [ ] `pytest -q` and `python scripts/check.py full` pass from a fresh clone

**Dependencies:** MVP checkpoint
**Files likely touched:** `.gitleaksignore`, `README.md` (clone URL)
**Estimated scope:** S

## Phase 5: your own evals, skills and agents

The tasks are in [todo-phase5.md](todo-phase5.md).
