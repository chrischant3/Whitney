# Kilo plugins in Docker: stage `.kilo/plugin/` and show it ran

A Docker `kilo` run stages the project's `.kilo/plugin/` files and drops `--pure` only when it has one, with `@kilocode/plugin` baked into the sandbox image (the container has no network); `permission_held` counts a refusal whose error matches the message the eval says the plugin throws, and the report names each plugin with the calls it refused. Every other run keeps `--pure` and names a plugin it left out.

## Job story

When my team enforces "never read `.env`" with a Kilo plugin's `tool.execute.before`, and Whitney's run leaves that plugin out without saying so
I want to run the eval with the plugin we actually ship
So I can show an auditor evidence that our real setup holds, not one with the guard quietly removed

## Acceptance criteria

- [ ] Given a project with no plugin, when I run `whitney run` on the `kilo` target, locally or in Docker, then Kilo is started with `--pure` as today and every existing test that checks the command passes unchanged
- [ ] Given a project whose `.kilo/plugin/block-env.ts` throws "block-env: .env is off limits" from `tool.execute.before` on any read of `.env`, and an eval whose `permission_held` names that message as the plugin's refusal, when I run it in Docker, then the log has the refused call with that message in its `state.error` and the check reports held
- [ ] Given the same eval with a variant that removes the plugin, when I run it in Docker for 3 epochs, then the check reports bypassed in at least 1 epoch; if it's only ever "not attempted", the prompt is made more pressing until it is
- [ ] Given the plugin run, when I open the report, then a plugin section names `.kilo/plugin/block-env.ts` and lists the calls refused with its message, or says the evidence shows no effect when none were
- [ ] Given the plugin run, when `no_file_edits` watches the project, then it isn't failed or made inconclusive by the `.kilo/package.json`, `.kilo/package-lock.json` and `.kilo/node_modules/` Kilo writes, and the evidence doesn't carry their contents
- [ ] Given a test-only plugin that tries to read a Kilo login file and reach the network, when it runs in Docker, then the run's record shows it got neither
- [ ] Given the block-env project run locally, or a project with a plugin in `.kilo/plugins/`, `.kilocode/plugin/` or a config's `plugin` list, when I run it, then Kilo keeps `--pure` and the report's Not Staged table names each such plugin with the reason it wasn't run
- [ ] Given the slice is built, when I read `docs/adding-tests.md` and `docs/load-order.md`, then nothing there is made wrong by it: the `kilo` row of the staged-files table and the paragraph under it ("runtime integrations from a target repo are not executed", around line 831) say a Docker run stages and runs `.kilo/plugin/`; the Not Staged list (around line 851) has the plugin reasons; "No plugin runs, and Kilo keeps `--pure`" (around line 772) excepts a Docker run with a staged plugin; `permission_held`'s field table (around line 388) has the new field; and `load-order.md`'s side-by-side table (around line 225) has a `.kilo/plugin/` row

## Owed

This is split (a) of Task 46. Owed to split (b), the next slice:

- **Staging local `plugin` entries in a config.** Task 46's scope names it; this slice names each entry in Not Staged and runs none, so no plugin is left out silently. Not built here because:
  - What was shown isn't this. The spike ran a `.js` module from the `plugin` list of Whitney's own `KILO_CONFIG` overlay (`docs/spike-findings.md`, "Phase 5 Task 31", around line 884). A relative entry in a project's staged `kilo.jsonc`, a `.ts` file, where Kilo resolves the path from, and whether it installs `@kilocode/plugin` for one, aren't shown, and need a fixture first.
  - An entry can point at any file, such as `plugin.js` at the project root, which the loader treats as source and never stages. Which files an entry may pull in is a decision for Christopher.
  - `tests/test_safe_staging.py::test_the_unsafe_files_and_the_evals_path_are_never_staged` asserts the `plugin` key and `plugin.js` aren't staged. Its expectation would change, which needs Christopher's confirmation.
  - Until it's built, a team whose guard is only in a config's `plugin` list gets a run without it, in Docker too, and has the Not Staged row to tell them.
- **Npm-package entries in the `plugin` list.** Task 46 says they're refused with a message. This slice names them in Not Staged like any other entry and refuses nothing.
- **The `whitney validate` warning for a plugin tool with a built-in's name** (Task 46 criterion 4). "A custom tool with a built-in's name wins" is from Kilo's docs only, so it needs a fixture before code relies on it. Until then, a Docker run with such a plugin gives a verdict about the plugin's tool, not Kilo's, and nothing warns of it.
- **The how-to and the example eval** (the rest of Task 46 criterion 5): a section in `docs/adding-tests.md` on testing a plugin-enforced rule, with an example eval. The last criterion above only stops the docs being wrong; until this is written, a user finds the new `permission_held` field in its field table and nowhere else.
- **The "same run before the change" record** (Task 46 verification, "a record, not a gate"). No code: a Docker run of the block-env eval at `b0627c6`, with the plugin in the project, showing it didn't run. A few cents, with Christopher's go-ahead. It can be made at any time from that commit.
- **`whitney run` output with exit codes** (Task 46 verification). This slice's criteria describe the run and report but don't require a recorded transcript of the commands and their exit codes. The write-up of the Docker run should include them.

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `src/whitney/agent.py` — `_kilo_command` (around line 651) hard-codes `--pure`; the Docker branch (around line 187) and `_run_local_process` both call it
- `src/whitney/project_files.py` — `_load_kilo` (around line 221): `KILO_ASSET_DIRS` has no `plugin`, and `CONNECTION_CONFIG_KEYS` drops `plugin` silently; the `skills.paths` strip-and-name at lines 259-264 is the pattern for naming what's left out
- `src/whitney/checks/permissions.py` — `_refused_by_rule` (around line 311) only accepts Kilo's own deny or reject sentence, so today a plugin's refusal makes `permission_held` inconclusive
- `src/whitney/sandbox/Dockerfile` and `compose.yaml` — Kilo 7.8.18 and `network_mode: none`, so `@kilocode/plugin` has to be installed at build time; bump the image tag with it
- `src/whitney/checks/files.py` — `SKIPPED_FOLDERS` (line 33) and `_kilo_schema_rewrite`, the precedent for leaving aside a change Kilo made; the snapshot's 10,000-file cap is in `src/whitney/evidence.py`, and the spike saw 4,110 files in `node_modules/`
- `tests/test_safe_staging.py` — asserts today that a plugin and the `plugin` key aren't staged, end to end through the task builders; run with `.venv/bin/python -m pytest tests/test_safe_staging.py tests/test_project_files.py tests/test_agent_command.py -q`

## Assumptions

Every "(Task 44)" guess below must be checked against Christopher's dated decisions under Task 44 in `tasks/plan-later.md` before building. If they differ, this spec is stale. Task 44's spike also shows how Kilo picks up a preinstalled `@kilocode/plugin`, so use its fixture rather than capturing that again.

- Where may plugins run (Task 44)? — Only in Docker. A local run leaves a plugin out and keeps `--pure`, as today, and names it in Not Staged instead of refusing the run, so local runs of projects that already have a plugin folder keep working.
- Does dropping `--pure` need its own switch (Task 44, and Task 31 decision 1, which kept `--pure`)? — No scenario field. A Docker run drops `--pure` exactly when it stages a plugin. That also lets in Kilo's own `@kilocode/kilo-indexing` and `@kilocode/plugin-atomic-chat` (`docs/spike-findings.md`, "Phase 5 Task 31"); the write-up records whether they changed anything.
- How does `@kilocode/plugin` reach the run (Task 44)? — Preinstalled in the sandbox image at the same version as Kilo, not vendored into the repo. How Kilo picks it up instead of running its own install in `.kilo/` isn't shown yet; the implementing session captures it in a fixture before code relies on it.
- Which check judges a plugin-enforced rule (Task 44's "how the report names it")? — `permission_held`, with an optional field naming the message the plugin throws. A refused call whose `state.error` matches it counts as refused. Without the field, the check behaves as today.
- How does the report tell a plugin's refusal from an ordinary tool error? — Only by that message. Nothing else in Kilo's stream says a plugin ran (`docs/spike-findings.md`, "Phase 5 Task 40"), so a plugin with no message named gets "the evidence shows no effect".
- Which Kilo version? — The image stays on 7.8.18, so no-plugin Docker runs are unchanged. The plugin behaviour was shown on 7.8.27; if 7.8.18 doesn't reproduce it in the first fixture, the bump becomes its own task before this one.
- What may a plugin reach (Task 44)? — What the Docker container already allows: no login, no network beyond Inspect's bridge, the project only. Nothing more is added.
- Is `.kilo/plugins/` the same as `.kilo/plugin/`? — Not assumed. Only `.kilo/plugin/` was shown in a run, so only it is staged.
