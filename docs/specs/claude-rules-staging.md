# Stage `.claude/rules/` rules that load at launch

On `claude-code`, stage the `.md` rules in `.claude/rules/` that have no `paths` field, using the asset walk that already stages `.claude/skills/` and `.claude/agents/`. Path-scoped rules stay out, and the report says why. A capture of Claude Code's first request comes first, because it decides two things the docs alone say: whether rules in subfolders count, and whether a path-scoped rule stays out of the launch prompt.

## Job story

When I keep a team rule in `.claude/rules/` and run Whitney on `claude-code` to prove the agent follows it
I want the run to give the model the rule Claude Code would give it at launch
So I can trust a pass or a fail, instead of testing a project that's quietly missing the rule

## Acceptance criteria

- [ ] Given a project run as itself (not staged), with one marker each in `.claude/rules/top.md` and `.claude/rules/nested/deep.md` (no `paths` field), `.claude/rules/scoped.md` (a `paths` field) and `.claude/rules/imported-scoped.md` (a `paths` field, imported by `CLAUDE.md`), when Claude Code runs once through the logging proxy and no file is read, then a write-up under `docs/results/` records which markers were in the first request, with the Claude Code version. This is done before the loader changes, and the criteria below follow it: a kind of rule is staged only if this capture or P1 showed it sent at launch. If `scoped.md` was sent at launch, stop and ask Christopher, because "leave out and name" would then test less than the project does.
- [ ] Given a project with `.claude/rules/testing.md` that has no `paths` field, and an eval file whose `source` is `.claude/rules/testing.md`, when I run `whitney validate --target claude-code`, then it reports no problem for that eval file; and when I run it with `--target kilo`, then the eval file doesn't apply to `kilo` (a `.claude/rules/` entry in `ONE_TARGETS_FOLDERS`, `src/whitney/evalfile.py:64`), unless a `kilo debug config` capture, recorded in `docs/load-order.md` with the Kilo version, shows Kilo reads `.claude/rules/`.
- [ ] Given that project, when I run the eval on `claude-code` with `--capture-prompt`, then the report's staged files list `.claude/rules/testing.md`, its "Not Staged" table no longer names `.claude/rules/` as "rest of .claude/", and the "What was sent to the model" section says whether the rule's text reached the model.
- [ ] Given a rule in `.claude/rules/` with a `paths` field, a file there that doesn't end in `.md`, a symlink there, or a file there that isn't UTF-8, when I run a `claude-code` eval, then none of them is staged and the report's "Not Staged" table names each one with the reason; a rule in a subfolder is staged if the launch capture showed it sent, and named there with the reason if not; and when one of them is an eval file's `source`, then `whitney validate` says it isn't staged and gives that reason.
- [ ] Given a `CLAUDE.md` that imports `@.claude/rules/style.md` and `@.claude/rules/scoped.md`, where `scoped.md` has a `paths` field, when the project is staged for `claude-code`, then `style.md` is staged with the `CLAUDE.md`, and `scoped.md` is staged too if the launch capture showed `imported-scoped.md` sent, and named in "Not Staged" if not; and an import of `.claude/settings.json`, `.claude/commands/` or `.claude/hooks/` is still named as configuration (`CONFIG_IMPORT_REASON`).
- [ ] Given an eval whose rule lives only in `.claude/rules/`, when I run it for real on `claude-code`, then the template variant holds, the baseline without the rule fails in at least 1 epoch, and a write-up under `docs/results/` records both runs with the Claude Code version.
- [ ] Given the change is made, when I read Task 47 in `tasks/plan-later.md`, `docs/load-order.md`, `docs/adding-tests.md` and `docs/specs/package-and-expected-results.md`, then Task 47 records the decision with its date, naming `test_an_import_of_a_file_claude_code_reads_as_configuration_is_named_not_staged` first as the test whose expectation changes; the `.claude/rules/` rows say rules with no `paths` field are staged and path-scoped ones are named, give the launch capture as the evidence, and mark when a path-scoped rule loads as taken from Claude Code's docs and not captured; and the spec's rules for `source` allow a rule file.

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `src/whitney/project_files.py`:
  - `CLAUDE_CODE_ASSET_DIRS` (L80) and `_claude_code_assets` (L623) stage skills and agents.
  - `_unstaged_claude_entries` (L1179) names `rules/` "rest of .claude/".
  - `_importable_in_claude_dir` (L566) is why an import of a rules file is refused today.
- `src/whitney/evalfile.py`: `ONE_TARGETS_FOLDERS` (L64) is how `.claude/agents/` applies to `claude-code` alone.
- `src/whitney/capture.py`: `texts_to_find` (L421) already treats any staged source that isn't a skill or an agent as an instruction file and looks for its text. The "sent to the model" line may need nothing new.
- `tests/test_claude_code_imports.py`: L318-331 is the parametrized test whose `.claude/rules/style.md` case changes, and it shows how tests are written here. Run `.venv/bin/python -m pytest -q tests/test_claude_code_imports.py tests/test_project_files.py`, then `python scripts/check.py task`.
- `tests/test_project_files.py`: `test_each_name_left_out_has_its_reason` (L601) is the pattern for asserting a "Not Staged" reason.
- `docs/results/2026-11-12-capture-followups.md`: the P1 capture, where Claude Code 2.1.322 sent `.claude/rules/style.md` under its own header. That was one rule, directly in the folder, with no frontmatter. P1 had no rule in a subfolder, no path-scoped rule and no import of a rules file, so it shows nothing about those.
- `spikes/capture_followups.py`: `run_import_case` with mode `"real"` (L213) made that capture: `claude -p` in the project folder, through `prompt_capture`'s logging proxy, with each marker looked for in the first request. The launch capture needs only the first request, so it doesn't wait for Task 60. One Haiku run, about 3 cents.
- `docs/load-order.md` L93: Kilo doesn't read `.kilo/rules/nested/<name>.md`. That's the repo's only capture of a nested rules folder, and it's Kilo's, so it doesn't answer for Claude Code.

## Owed

- Task 47's plan asks for a real capture showing a path-scoped rule reaching the model only after a matching file is read. This slice captures the launch half (the rule isn't in the first request). It doesn't capture the other half, the rule arriving in a later request once a matching file is read, which needs the raw spike proxy's later requests or Task 60. So that box of Task 47 stays open, and until it's ticked nothing Whitney prints or documents may say as fact when a path-scoped rule loads.
- Claude Code's docs say a symlink in `.claude/rules/` is followed. Whitney never stages through a link, so a symlinked rule is left out and named, and a run tests less than the project there. No capture has checked the symlink case.

## Assumptions

- Should rules with no `paths` field be staged at all? Yes. Claude Code sends them at launch (P1 capture), so a run without them tests less than the project does. Christopher still has to confirm this, because a test's expectation changes (H1).
- How should path-scoped rules be treated? Leave them out and name them, if the launch capture shows one isn't sent at launch. The reason says what was captured and marks the rest as the docs' word: "has a `paths` field, so Claude Code doesn't send it at launch; its docs say it loads when the agent reads a matching file, which Whitney hasn't captured and doesn't stage".
- Only `.md` files directly in `.claude/rules/`, or subfolders too? Not known yet. Claude Code's memory docs say rules in `.claude/rules/` are found recursively, but P1 held one rule directly in the folder, and Kilo reads only the files directly in its own rules folders. The launch capture decides: subfolders are staged if `nested/deep.md` was sent, and named as not staged if it wasn't.
- Is an imported path-scoped rule staged? Expected yes, since an import puts a file's text into the importing file at launch. But no capture has an import of a file with a `paths` field, or of any file under `.claude/rules/`, so the launch capture's `imported-scoped.md` decides.
- Does the `AGENTS.md` eval's baseline, which on `claude-code` stands for every instruction file, now remove rules too? No. A rule file is its own `source` with its own eval file, as a skill is.
- What's the eval file name for a rule? `rules/<name>-md.json`, the same as for `.kilo/rules/<name>.md`.
