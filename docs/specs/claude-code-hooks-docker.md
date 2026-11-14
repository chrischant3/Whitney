# Claude Code PreToolUse hooks: stage them in Docker and credit their refusals

An eval file can opt in to staging the project's `.claude/settings.json` `hooks` block and the scripts in `.claude/hooks/`, on Docker runs only. That lets `permission_held` judge a rule enforced by a `PreToolUse` command hook, and the report names each hook and whether the stream shows it ran. It goes through the existing settings cut-down and `permission_held` rather than a new check. It needs 1 new Docker capture, since no saved stream has a hook refusing `git push` with the hook events on.

## Job story

When my team's "never push" rule lives in a `PreToolUse` hook rather than a permission rule, and Whitney leaves the hook out so the run says the agent pushed
I want to run the eval with the hook in place and see that it was the hook that refused the push
So I can trust a held or bypassed result for the setup we actually run, instead of reading a result that doesn't match it

## Acceptance criteria

- [ ] Given a project whose `.claude/settings.json` has a `PreToolUse` command hook on `Bash` that refuses `git push` with a script in `.claude/hooks/`, and no permission rule for it, and an eval file that opts in to hooks with a `permission_held` scenario that names the hook as its source, when I run `whitney run --execution docker` and the agent tries to push, then the scenario is held, its explanation says the `PreToolUse:Bash` hook refused the push, and that refusal doesn't make the run count as ended early or inconclusive.
- [ ] Given that project with a deny rule `Bash(git push:*)` added, and a `permission_held` scenario for the rule, when only the hook's refusal is in the stream, then that scenario is inconclusive, as it is today: the rule gets no credit for the hook's refusal.
- [ ] Given the hook-only project and eval file from the first criterion, when a variant turns hooks off, or the eval file doesn't opt in, then the hook doesn't run, a push the agent tries makes the hook-source scenario bypassed, and the report's "Not Staged" table still lists `.claude/settings.json (hooks)` with the reason it was left out.
- [ ] Given the eval file that opts in, when I run it with local execution, then `whitney run` stops before any model is called and exits non-zero with a message saying hooks only run in Docker and how to run it there.
- [ ] Given a run that staged hooks, when I open the report, then it names each staged hook by event, matcher and command, and says for each whether the stream shows it ran and whether it refused a call (with the refused call), or that the evidence shows no effect.
- [ ] Given the criteria above, when I run `python scripts/check.py task`, then each is covered by a test that replays a captured stream, with no model call. The new capture is a Docker run of a `PreToolUse:Bash` hook refusing `git push` under `--include-hook-events`, saved with its `.meta.json` in `tests/data/claude-code-events/` beside the existing `hook-pretooluse-deny` and `hook-events-flag`.
- [ ] Given the hook-only project and eval file from the first criterion, with a second variant that turns hooks off as the control, when I run both in a real `whitney run --execution docker` over several epochs (about a dollar, with Christopher's go-ahead), then the scenario is held with the hook on and bypassed in at least 1 epoch with hooks off. If the control is only ever "not attempted", the prompt is made more pressing and the run repeated until it is bypassed. A write-up under `docs/results/` records the prompt used and each epoch's result for both variants.
- [ ] Given a staged test-only hook that tries to read the host's Claude login and API key and to write a file outside the project, when it runs in the Docker container in a real run (cents, with Christopher's go-ahead), then what it recorded shows neither the login nor the host's key, and no file outside the project changed on the host.

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `src/whitney/project_files.py`: `_claude_code_settings` (around line 1132) cuts the settings down to `permissions` and names `hooks` with `SETTINGS_REASONS`. `_unstaged_claude_entries` names `.claude/hooks/` as "rest of .claude/".
- `src/whitney/claude_code.py`: `_claude_command` builds the argv that local and Docker runs share. `_outcome` marks any listed denial `ended_early`, and that's why a hook refusal gets misread today. The constructor already refuses Docker-only misuses with a `ValueError`.
- `src/whitney/checks/permissions.py`: `_claude_held` and `CLAUDE_REFUSED`. A hook's "PreToolUse:Bash hook error:" refusal falls into "other" and makes the verdict inconclusive.
- `tests/data/claude-code-events/hook-pretooluse-deny.meta.json`: the shape of a captured hook refusal under Whitney's flags, with the settings and scripts in `project_files`. Its hook gates tools on a skill, not on `git push`, and it was captured on the host without `--include-hook-events`.
- `tests/test_checks_permissions_claude_code.py`: the nearest tests. `test_a_listed_refusal_in_other_words_keeps_a_refusal_from_passing` covers the hook case as it works today. Run it with `python -m pytest -q tests/test_checks_permissions_claude_code.py`, and run `python scripts/check.py task` before handing back.
- `docs/spike-findings.md`: the "Phase 5 Task 40" section (around line 485) covers what the stream records for each hook, with and without `--include-hook-events`, and the risk a hook carries.

## Assumptions

Every "(Task 44)" guess below must be checked against Christopher's dated decisions under Task 44 in `tasks/plan-later.md` before building. If they differ, this spec is stale. Task 44's spike also saves a Docker `PreToolUse:Bash` hook refusing `git push` under `--include-hook-events` in `tests/data/claude-code-events/`, so reuse that fixture rather than capturing a second one.

- Where may hooks run (Task 44)? — Docker only. A local run with hooks opted in is refused, and there's no local flag in this slice.
- Are hooks opt-in (Task 44)? — Yes, per eval file, and a variant can turn them off. With no opt-in, every existing result is unchanged.
- What can a hook reach (Task 44)? — What the Claude Code container already gives a run: the bridge's key, not the host's, no network beyond the model's, and only the project.
- Which check judges a hook-enforced rule (Task 44)? — `permission_held`, with a parameter naming a hook as the source, rather than a new check.
- Which hooks are staged? — Command hooks in the project's `.claude/settings.json`, with their scripts under `.claude/hooks/`. User-level hooks stay out (`--setting-sources project`). So do `prompt` and `agent` hooks, and scripts kept anywhere else.
- How does the report tell a hook ran? — Runs with hooks staged pass `--include-hook-events`, whose `hook_started` and `hook_response` events name each hook that ran.

## Owed

- Docs for the opt-in: `docs/adding-tests.md` should say how to turn hooks on and which check judges a hook-enforced rule, with an example eval. Until that's written, the only place a user can find the opt-in is `whitney validate`'s schema, so the docs decide whether anyone can actually use this slice. They're split (b) of Task 45 in the plan.
- The third version of Task 45's real "never push" run: hooks still opted in, with a variant that removes the hook from the project's `.claude/settings.json`. This slice's real run has the other 2 versions (hook on, and hooks turned off by a variant). The third is left out because it shows the same thing as the hooks-off control, a push going through with no hook, and what it adds is a check on variant file overrides, which this slice doesn't build. Until it's run, nothing shows on a real harness that an opted-in run with no hook in its settings behaves like a run with hooks off.
- Rule 3 for real, from Task 45's verification: Task 40's `UserPromptSubmit` reminder hook against `skill_loaded`, with and without the hook, with the rates recorded. It's left out because it's a different hook event judged by a different check, and the job story is about a `PreToolUse` hook refusing a push. This slice stages the whole `hooks` block, so a `UserPromptSubmit` command hook does run in an opted-in Docker run, but no criterion here shows its effect on `skill_loaded`. It's split (b) of Task 45.
- Task 45's criterion that Task 40's rule 3 changes from "not tested by Whitney" to tested, where the hook is staged. It can't be met before the rule 3 run above, since that run is the evidence for "tested". This slice leaves `src/whitney/improve.py`, `tests/test_improve.py::test_the_section_reads_as_recorded` and `tests/data/improve/skill-claude-code.md` unchanged. So after this slice the rule 3 advice still says "Whitney stages no hooks, so a Whitney run leaves the hook out", and `docs/improvement-rules.md` and `docs/adding-tests.md` say the same in their own words. That's true by default and no longer true for an opted-in Docker run. Changing the wording changes what a word-for-word test expects, so Christopher confirms the new text before either file is touched. It's split (b) of Task 45.
