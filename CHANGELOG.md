# Changelog

What changed in each release, and what anyone who forks Whitney or runs it in CI must update.

## Unreleased

### Changed

- **The gate reads positive and negative trigger tests separately.** A file with trigger tests must now reach its minimum, and have enough conclusive runs, over its positive prompts and over its negative prompts, each on its own, as well as over its scenarios. Before, the two kinds were one pool, so 12 of 12 positive runs and 0 of 3 negative runs passed at 80%. The same logs can now fail: `whitney run` and `whitney report` exit 1 where they exited 0. The problem lines name the kind (`template negative trigger tests 0% is below the minimum 80%`, where they said `template trigger tests`), and the line under a file's tables gives a rate for each kind. Update anything that matches those lines. A kind with few prompts fails on one stray run, so give each kind enough prompts or epochs. For code that imports `whitney.report`: `TRIGGER_PART` is gone (`TRIGGER_PARTS` and `GATE_PARTS` replace it), and `EvalSummary.parts` has the keys `positive trigger test` and `negative trigger test` where it had `trigger test`.
- **A Claude Code run is graded differently when the eval file switches off a skill.** If the file's own `harness_permissions` denies `Skill(<name>)` and Claude Code refuses a call to that skill, the run is no longer inconclusive. The same logs' runs were inconclusive before, and `whitney run` could exit 1 where it now exits 0. Every other permission denial still makes the run inconclusive. Only exact names are read: a rule with a pattern, such as `Skill(review *)`, is treated as before.
- **A failed `Agent` call that names no agent** no longer makes `subagent_delegated` and `subagent_not_delegated` inconclusive on Claude Code.

### New

- **The report shows each skill or agent an eval file switched off,** on both targets: how many calls the harness refused, in how many runs, and the harness's own words. See `docs/adding-tests.md`, "Switching off a built-in skill or agent".

## 0.2.0

The first tagged release. Whitney becomes a command you install and point at your own project, and its checks grow from one rule to nine kinds of evidence.

### New

- **The `whitney` command**, with `validate`, `run`, `report`, `checks` and `--version`. Install it from the tag; see the README, "Install from a release".
- **Your own project and evals.** `--project-path` names the project under test and `--evals-path` the folder of eval files. Whitney copies the project's configuration and never writes to it.
- **Eval files, version 2.** A file names its `source`, its `variants` (a baseline or a control), and the checks each scenario expects.
- **Nine checks** an eval file can name: `no_secret_committed`, `no_string_added`, `no_file_edits`, `outside_canary_not_reached`, `skill_loaded`, `skill_not_loaded`, `subagent_delegated`, `subagent_not_delegated` and `permission_held`. `whitney checks` lists them with their parameters.
- **Skills and agents on Kilo.** Scenarios and trigger tests for a skill or an agent definition. The gate reads a file's scenarios and its trigger tests separately.
- **Permission rules on Kilo.** `permission_held` tests a rule in `kilo.jsonc` against a control that sets it to `allow`.
- **A gate with minimums.** Each check has a minimum pass rate, and a file needs enough conclusive runs. `whitney report` applies the same gate to saved logs.
- **The `claude-code` target**, for rules in `AGENTS.md`.
- **A consumer example** in `examples/consumer`, with recorded real runs under `docs/results/`.
- **A security policy.** `SECURITY.md` says how to report a vulnerability privately, and what is in scope.

### What you must update

- **`docker/` moved to `src/whitney/sandbox/`.** If you build the sandbox image by path, change `docker/Dockerfile` to `src/whitney/sandbox/Dockerfile`.
- **`--target` and `--model` have no default.** A run that names neither exits 2.
- **`python scripts/run_evals.py` is deprecated.** It still works with its old flags and defaults. Use `whitney run`.
- **Version 1 eval files still load.** They are upgraded as they are read. New files should say `"version": 2`.

### Known limits

- Skills, agents and permission rules are tested on Kilo only.
- `permission_held` reads a `bash` or `task` rule written as a pattern, and `edit` or `external_directory` set to one action. A plain `deny` on a tool can't be tested yet.
- Two checks, `skill_not_loaded` and `subagent_not_delegated`, haven't failed in a real run.
