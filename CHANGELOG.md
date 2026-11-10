# Changelog

What changed in each release, and what anyone who forks Whitney or runs it in CI must update.

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

### What you must update

- **`docker/` moved to `src/whitney/sandbox/`.** If you build the sandbox image by path, change `docker/Dockerfile` to `src/whitney/sandbox/Dockerfile`.
- **`--target` and `--model` have no default.** A run that names neither exits 2.
- **`python scripts/run_evals.py` is deprecated.** It still works with its old flags and defaults. Use `whitney run`.
- **Version 1 eval files still load.** They are upgraded as they are read. New files should say `"version": 2`.

### Known limits

- Skills, agents and permission rules are tested on Kilo only.
- `permission_held` reads a `bash` or `task` rule written as a pattern, and `edit` or `external_directory` set to one action. A plain `deny` on a tool can't be tested yet.
- Two checks, `skill_not_loaded` and `subagent_not_delegated`, haven't failed in a real run.
