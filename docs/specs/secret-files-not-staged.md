# Keep secret-named files out of staged folders

Apply the existing `SECRET_NAMES` list (today used only for Claude Code `@imports`) to the skill, agent and rules folders a run stages, on both targets. Each file left out is named in the report's "Not Staged" table, and Markdown files are exempt so a skill about handling secrets can still be tested.

## Job story

When I stage a project whose skill, agent or rules folder holds a file like `.envrc`, `.npmrc` or `deploy.pem`
I want Whitney to leave that file out of the run and tell me it did
So I can run evals without the file's contents reaching the agent or being kept in the `.eval` log

## Acceptance criteria

- [ ] Given a skill folder holding a `.envrc` with a made-up value, when I run `whitney run` on the `kilo` target, then the made-up value appears nowhere in the run's `.eval` log and the report's "Not Staged" table has a row for that eval variant naming `.envrc` with the existing import reason (`SECRET_IMPORT_REASON`: its name looks like a key's or a login's). The same run on `main` before the change finds the made-up value in the log, which shows the check can fail, and the write-up under `docs/results/` records `whitney run`'s exit code for both runs
- [ ] Given the same project, when I run `whitney run` on the `claude-code` target, then the same is true
- [ ] Given a secret-named file (such as `deploy.pem` or `credentials.json`) in an agent folder, when I run `whitney run` on either target, then that file isn't staged and is named in "Not Staged" with the same reason, and the folder's other files are staged as before
- [ ] Given a skill folder holding `references/secrets.md` and `references/credentials.md`, when I run `whitney run`, then both are staged and neither appears in "Not Staged"
- [ ] Given a project with no secret-named files outside Markdown, when I run `whitney run` or `whitney validate`, then staging and output are unchanged; `whitney validate` on `examples/consumer` still ends `0 problem(s), 0 warning(s)`
- [ ] Given the existing tests and eval fixtures, when the change lands, then no test's expectation has changed without Christopher naming and confirming it first, and no eval fixture's bait file is dropped from a run
- [ ] Given I read `docs/adding-tests.md`, then it lists the names that are kept out of every staged folder and says Markdown files are exempt

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `src/whitney/project_files.py` — `SECRET_NAMES` and `_looks_secret` (used only for imports today), `_is_safe_file` (`.env` only), and `_walk_files`, which drops files with no "Not Staged" row and also serves `load_template_files`, so changing it reaches the template loader too. Kilo `instructions` matches use `_is_safe_file` directly. `_direct_md_files` (Kilo rules and workflows) keeps only `.md` files, so it needs no change. The rows come from `files_not_staged`, `files_not_staged_reasons` and `not_staged_reason`, which name only non-UTF-8 skill files for Kilo today, so agent folders on Kilo need new plumbing there
- `tests/test_claude_code_imports.py` — the parametrized secret-name import test (around line 366), the closest prior art: name, reason and "Not Staged" row. Run with `.venv/bin/python -m pytest -q tests/test_claude_code_imports.py tests/test_safe_staging.py tests/test_project_files.py`
- `tests/test_safe_staging.py` — the nearest end-to-end staging test, with a fake `kilo` run through `inspect_eval`
- `src/whitney/report.py` — the "Not Staged" table (around line 1008) that a new left-out row must reach
- `docs/adding-tests.md` — the staging rules and the secret-name list for imports (around lines 828 to 861)

## Owed

- A `whitney validate` warning for each secret-named file (Task 61's criterion "`whitney validate` warns about each one, so a team sees it before a run"). This slice names each file in the report's "Not Staged" table instead, so the file stays out of the log either way, but a team only learns of it after a run. The warning would also need a Claude Code branch in `src/whitney/cli.py`, whose extra warnings are Kilo-only today.
