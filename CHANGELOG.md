# Changelog

What changed in each release, and what anyone who forks Whitney or runs it in CI must update.

## Unreleased

### Changed

- **A Claude Code run is graded differently when the eval file switches off a skill.** If the file's own `harness_permissions` denies `Skill(<name>)` and Claude Code refuses a call to that skill, the run is no longer inconclusive. The same logs' runs were inconclusive before, and `whitney run` could exit 1 where it now exits 0. Every other permission denial still makes the run inconclusive. Only exact names are read: a rule with a pattern, such as `Skill(review *)`, is treated as before.
- **A failed `Agent` call that names no agent** no longer makes `subagent_delegated` and `subagent_not_delegated` inconclusive on Claude Code.

### New

- **`outside_canary_not_reached` works on the `claude-code` target.** It reads Claude Code's tool calls, their results and its permission denials, a subagent's included. A file that uses it applies to `claude-code` now, so `whitney run --target claude-code` runs it where it used to list it under "Not run for this target". Claude Code doesn't guard shell commands, so a shell read outside the project fails the check unless a `Read` deny rule refuses it; `docs/adding-tests.md` says how.
- **Docker mode for the `claude-code` target.** `--execution docker` runs a pinned Claude Code in a container with no network, served by Inspect's bridge, so the agent can't reach the host's files or the internet and the API key stays on the host. It was refused before ("the claude-code target runs locally only"). It needs the `gateway` extra, and the run stops at once if the `anthropic` package is missing. `scripts/run_evals.py` accepts it too.
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
