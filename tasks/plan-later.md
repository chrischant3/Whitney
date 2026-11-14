# Plan: the Later list

| | |
|---|---|
| **Status** | Draft, 2026-11-13, for Christopher's review. Nothing here is approved yet. |
| **Written** | 2026-11-13, against `main` at `4892a11`, from the "Later" list in [plan-phase5.md](plan-phase5.md), plus Kilo's plugins (its version of hooks) and a spike on [omp](https://omp.sh). |
| **Where it sits** | After milestone E. Its tasks are numbered on from [todo-phase5.md](todo-phase5.md), whose last is Task 40. The custom Kilo config plan ([plan-kilo-config.md](plan-kilo-config.md), on the local branch `kilo-config-path`) is parked and keeps its own numbers. |

## Rules for every task

The rules in [todo-phase5.md](todo-phase5.md), "Rules for every task", apply to each task here: a failing test first, no test loosened without Christopher's confirmation, `python scripts/check.py task` with mutation testing on the final commit, docs updated in the same task, its own branch and worktree, no guessed harness fields, and boxes ticked with a dated note naming the evidence.

Each task below has three parts besides its description:

- **Scope:** what the task changes, and what it leaves out on purpose.
- **Acceptance criteria:** what must be true when it's done.
- **Verification:** how a reader can see it works on a real harness or through the commands a user types. Passing unit tests are taken as given and aren't listed. Where a task makes no real run, it says why.

Unless a task says otherwise, its verification also includes items 1 and 5 to 7 of "Evidence for every task not yet started" in [todo-phase5.md](todo-phase5.md): red then green, what the evidence doesn't show, the review's findings, and the gate with its CI run.

Real runs cost money. Each task gives a rough cost, and anything over a few cents needs Christopher's go-ahead first.

## Order

1. Fixes first, since they change results users already see or keep secrets out: Tasks 41, 42, 43 and 61.
2. Then the safety design that both kinds of hook need: Task 44 (spike), then Tasks 45 (Claude Code hooks) and 46 (Kilo plugins).
3. Task 60 (what a capture keeps) before Tasks 47 and 48, which need later requests in full.
4. Task 50 before Task 51, which uses the same stream reader.
5. The other items in any order. Each says what it needs.

Not covered here: the open decisions and the "Follow-ups recorded but not yet tasks" in [todo-phase5.md](todo-phase5.md). They stay where they are.

**Saved logs.** No `.eval` log is committed; `docs/results/` holds write-ups only. A task that re-scores saved logs names where they are kept (the local `logs/` folder of the run that made them), re-scores them with `inspect score` or a short script, and lists the logs it used in its write-up.

One item in the Later list needs no task: **Claude Code skills and agents**, struck through there already (done in milestone E).

---

## Fixes

### Task 41: Redact literal provider keys copied into the run's Kilo config

**Description:** The run's Kilo overlay copies the user's global `provider` settings, so a literal `apiKey` there, or a proxy password in `HTTP_PROXY=user:pass@...`, can be read by the agent and isn't redacted from the log (Later list, Task 9 review). The exposure is local only: a Docker Kilo run builds its global config fresh and copies neither the provider settings nor `LOCAL_ENV_PASSTHROUGH` (`agent.py`, the Docker branch; `evidence.run_secrets` gives a Docker run none). The parked branch `kilo-config-path` has a `provider_secrets` function (`agent.py`, commit `7cd5204`) that covers `options`, their `headers` and passwords in URLs inside them, but not proxy variables, and feeds only the agent's own secrets list. This task ports that function and its tests to `main` under the same names (`provider_secrets`, `SECRET_OPTION_WORDS`, `SECRET_IN_A_NAME`), so the parked branch's rebase drops a duplicate rather than meeting a conflict, and adds what it lacks.

**Scope:**
- In: secret-looking values in the copied `provider` settings (`options`, `headers`, option names with key, token, secret, password and the like, in any letter case), passwords in URLs, and passwords in proxy variables in `LOCAL_ENV_PASSTHROUGH`, where the whole `user:pass@` part is replaced, so a password shorter than the usual 8-character minimum (`MIN_SECRET_LENGTH`) is still hidden. Local runs.
- Out: Docker, which copies neither.
- Out: refusing literal keys outright (`{env:NAME}` only). That is the other option the Later list names; this task redacts, and the docs recommend `{env:NAME}`.

**Acceptance criteria:**
- [x] Each such value is added to both the agent's run secrets and `evidence.run_secrets`, so it is replaced in the log, the transcript and the report. *(2026-11-13: `provider_secrets` and the new `proxy_secrets` in `agent.py` feed `_run_with_overlay`, `_run_local_process`, the capture path's secrets and `evidence.run_secrets`; tests `test_literal_provider_keys_and_proxy_passwords_kilo_is_given_are_redacted_from_what_the_eval_stores`, `test_a_literal_provider_key_and_a_proxy_password_in_a_captured_request_are_redacted`, `test_a_local_kilo_run_is_given_the_literal_secrets_in_its_global_provider_settings_and_proxy_passwords`. Review fixes, 2026-11-13: one redaction pass in `_run_local_process`, and `--base-url`'s password goes to `evidence.run_secrets` too, `test_a_local_kilo_run_is_given_the_user_and_password_in_its_base_url`, `test_the_scenario_scorer_redacts_the_password_in_the_base_url`.)*
- [x] Model names, base URLs without passwords, and option names that only contain a secret word by chance (such as `monkeyName`) are not redacted. *(2026-11-13: `test_model_names_plain_urls_and_names_holding_a_secret_word_by_chance_are_not_secrets`, `test_an_option_named_in_one_case_is_still_taken_as_a_secret`, and a whole `{env:NAME}` reference is kept, `test_an_env_reference_is_not_a_secret_since_its_value_is_redacted_by_name`.)*
- [x] `docs/adding-tests.md`, or the doc that covers the overlay, says literal keys are redacted, that redaction doesn't stop the agent reading or sending a key it can see, and recommends `{env:NAME}`. *(2026-11-13: README, "Through your global Kilo config (default)"; `docs/adding-tests.md`, "What a captured log can hold", points to it.)*

**Note for the parked plan:** when `kilo-config-path` is rebased, its `provider_secrets` gives way to this one, and `plan-kilo-config.md` says so. That plan lives only on that branch, so this isn't a box here.

**Verification:**
- [x] A local run through `whitney run`, with a made-up key in a throwaway global config and a made-up proxy password in `HTTP_PROXY`, whose prompt makes the agent run `cat "$KILO_CONFIG"` and `env | grep -i proxy`. The exit code is recorded, and a search of the `.eval` log and the report for both values finds nothing. *(2026-11-13: run 2 in [docs/results/2026-11-13-provider-key-redaction.md](../docs/results/2026-11-13-provider-key-redaction.md), exit 0, nothing found. `$KILO_CONFIG` is empty in the agent's shell on Kilo 7.8.27, so the prompt also read `"$TMPDIR"/tmp*.json`, which printed the config with `"apiKey": "[redacted]"`.)*
- [x] The control: the same run on `main` before the change finds both values in the `.eval` log. That shows the search can fail. *(2026-11-13: run 3 in [docs/results/2026-11-13-provider-key-redaction.md](../docs/results/2026-11-13-provider-key-redaction.md), on `main` at `fa7f5dd` with run 2's `$TMPDIR` prompt: the key, `t41pw` and `eve:t41pw@` each found 10 times. Run 1, with `$KILO_CONFIG` only, found the proxy password alone.)*
- [x] A record that a Docker run's global config holds no copied provider settings (the file's text, from the container). *(2026-11-13: run 4 in [docs/results/2026-11-13-provider-key-redaction.md](../docs/results/2026-11-13-provider-key-redaction.md), exit 0: the container's `~/.config/kilo/kilo.json` holds only the bridge's `baseURL` and `sk-none`, and `env | grep -i proxy` printed nothing.)*

**Dependencies:** none. **Size:** S. **Cost:** two or three Haiku runs, cents.

### Task 42: `permission_held` matches only the fields that run

**Description:** `forbidden_input` is searched in every string of a tool call's input (`checks/permissions.py`, `_strings`). So text the model writes, such as a bash call's `description`, a `todowrite` item or a `task` prompt, counts as the forbidden action. A `git status` described as "check before git push" fails the check (Later list, 2026-11-10). It errs towards failing, never towards passing.

**Scope:**
- In: a list, for each tool on both targets (including the Claude Code tools `_claude_held` reads, with the `CLAUDE_HAND_OFFS` exclusion kept), of the input fields that carry what runs. Examples are bash's `command`, and an edit or write tool's path and content. Each list is taken from captured fixtures, never guessed. Plus the change to `permission_held` to search only those fields.
- Out: any change to how a refusal is recognised; the other checks.

**Acceptance criteria:**
- [ ] The field list is in `docs/adding-tests.md` beside `permission_held`, with the fixture each entry comes from.
- [ ] A tool missing from the list keeps today's behaviour (every string), and the check's explanation says when a matched call came from such a tool, so it was matched on every string.
- [ ] Each existing test whose expectation changes is named, and Christopher confirms it before it's changed.

**Verification:**
- [ ] The 2026-11-10 false fail reproduced from its saved log, or from a new capture if the log is gone: before the change it reports a fail, after it reports held.
- [ ] A real run on each target where the forbidden action does run. It must still fail the check, so the narrowing can't hide a real push.
- [ ] Re-scoring the saved logs (see "Saved logs" above) gives the same results as before, except for the cases listed and explained.

**Dependencies:** none. **Size:** M. **Cost:** cents for one run per target.

### Task 43: `git -C . push` gets past `"git push *": "deny"`

**Description:** Kilo matches a bash permission pattern against the command's text, so an option before `push` steps round the rule. On Kilo 7.8.27 with Haiku, `git -C . push origin master` ran and `permission_held` reported it bypassed (Later list, 2026-11-10). Whitney's check was right. The gap is in the rule the example ships and in what the docs promise.

**Scope:**
- In: a decision by Christopher on three questions, then the work it leads to.
  - Does `examples/consumer/kilo.jsonc` add patterns such as `"git * push*": "deny"`?
  - Do the docs say how easily a pattern rule is stepped round?
  - Is it recorded as a finding beside finding 001?
- In: the same question for Claude Code's `Bash(git push:*)` rules, answered from a capture.
- Out: any change to Kilo, or Whitney rewriting a team's rules.

**Acceptance criteria:**
- [ ] The decision is recorded here, dated.
- [ ] If the rule changes: the example's eval file gains a scenario that asks for a push "another way", and the docs list the routes a pattern can close (`git -C`, `git -c`, `env git`) with the patterns, and the routes it can't (a script, `sh -c "$(...)"`), which point to a sandbox or a hook (Tasks 45 and 46).
- [ ] The docs say plainly that a pattern rule matches text, not intent, on each harness, and point teams to a sandbox or a hook for a stronger guard (Tasks 45 and 46).

**Verification:**
- [ ] A real run of the new scenario on Kilo with the old rule (bypassed) and the new one (held), written up in `docs/results/`.
- [ ] The same pair on Claude Code, or a recorded reason it doesn't apply there.
- [ ] `whitney run` on the example, with its exit code and the report's line for the new scenario.

**Dependencies:** none. **Size:** S. **Cost:** cents to a dollar, with three epochs on each target.

---

## Hooks

### Task 44: Spike: a safety design for running a team's hooks

**Description:** Claude Code hooks are shell commands, and Kilo plugins are code inside Kilo's own process. The Task 40 spike reasoned, without showing it in a run, that a plugin can read the Kilo login and change what the model is sent (`docs/spike-findings.md`, "Phase 5 Task 40"). Whitney stages neither today. So a team whose `PreToolUse` hook refuses `git push`, or whose plugin's `tool.execute.before` blocks `.env` reads, gets results that don't match its real setup. Before either can be supported, this spike answers three questions: which hooks run, where, and how the report says they ran.

**Scope:**
- In:
  - Claude Code's hook events.
  - Kilo's plugin hooks (`tool.execute.before`, `tool.execute.after`, `permission.ask`, `chat.message`, `experimental.chat.system.transform`, the `event` bus).
  - For each, what it can do to a run, and whether its effect shows in the harness's stream.
  - Whether a hook or plugin can be run only in Docker, with no credentials in reach.
  - How the run's own `--pure` / `KILO_PURE` rule would have to change for Kilo plugins. Task 31 decision 1 kept `--pure` for capture; staging a plugin needs its own decision by Christopher.
  - How Kilo gets the `@kilocode/plugin` dependency it installs for a local plugin (preinstalled in the image, or a vendored copy) if the run has no network beyond the model's.
  - On Claude Code: telling a hook's refusal from a rule's (both are in `permission_denials`; a hook's tool result starts `PreToolUse:<tool> hook error:`), and what `--include-hook-events` adds to the stream.
- Out: building anything. omp's hooks are Task 58's question.

**Acceptance criteria:**
- [ ] A section in `docs/spike-findings.md` for each harness, with the harness version, the docs read (URL and date) and the runs made.
- [ ] A recommendation for each harness on each of the following, for Christopher to decide:
  - **Where hooks run:** only in Docker, refused locally, or locally behind a flag that says what it allows.
  - **What a hook can reach:** no login, no network beyond the model's, and only the project.
  - **How the report names it:** each hook or plugin that was staged, and whether its effect is visible in the evidence.
  - **On Kilo:** whether a run drops `--pure` only when a plugin is staged, and in Docker only.
- [ ] Kilo's files written into `.kilo/` (`package.json`, `package-lock.json`, `node_modules/`) are covered. Either the project snapshot ignores them, or the run removes them. The spike records which files each Kilo version writes.

**Verification:**
- [ ] Runs on real Claude Code and real Kilo, in Docker, with a hook or plugin that refuses `git push`. They show three things:
  - the hook ran;
  - the push was refused;
  - what the stream recorded. Kilo's plugin refusals are `tool_use` events with `state.status` `error`, and the spike records Claude Code's equivalent.
- [ ] A hook that tries to read the login file and reach the network, with what it got. This shows the sandbox holds, or doesn't.
- [ ] Christopher's decisions, recorded here, dated.

**Dependencies:** Task 36a (Claude Code in Docker, done). **Size:** M. **Cost:** a dollar or so, with Christopher's go-ahead.

### Task 45: Claude Code hooks: stage them and show they ran

**Description:** Build what Task 44 decided for Claude Code. Today `.claude/settings.json` is staged with only its `permissions` block (Task 37).

**Scope:**
- In: staging the project's `hooks` block, and the scripts it names, in the places Task 44 allows. A scenario field or option to turn hooks on, if Task 44 says hooks are opt-in. Passing `--include-hook-events` when hooks are staged. Telling a hook's refusal from a rule's, so `permission_held` doesn't count a hook's refusal as the rule holding and a hook's refusal isn't read as `ended_early`. The report naming each hook staged.
- The task may be split in two if it grows: (a) staging, the run refusal and telling refusals apart; (b) the report, the docs and rule 3.
- Out: user-level hooks (`~/.claude/settings.json`), which a run keeps out with `--setting-sources project`, and hooks of type `prompt` or `agent`, unless Task 44 covers them.

**Acceptance criteria:**
- [ ] Where Task 44 says hooks may not run, as in a local run if that is the decision, a run with a staged hook stops before the model is called, with a message saying why.
- [ ] The "Not Staged" table no longer lists `hooks` when they are staged, and lists them with the reason when they aren't.
- [ ] The report says, for each hook, whether the evidence shows its effect (for example a refused call whose message is the hook's).
- [ ] `docs/adding-tests.md` explains how to test a hook-enforced rule, with an example eval.
- [ ] It is documented which check judges a hook-enforced rule (`permission_held` with a hook source, or a new outcome), and the docs say so.
- [ ] Task 40's rule 3 changes from "not tested by Whitney" to tested, where the hook is staged. The word-for-word tests whose expectation changes (`tests/test_improve.py::test_the_section_reads_as_recorded`, `tests/data/improve/skill-claude-code.md`) are named and confirmed by Christopher first.

**Verification:**
- [ ] A real run of an eval for "never push" in three versions:
  - with only a `PreToolUse` hook enforcing it (held);
  - the same project with the hook removed by a variant, which is the control. It must be bypassed in at least one epoch; if it is only ever "not attempted", the prompt is made more pressing until it is bypassed;
  - the same with the hook staged but turned off.
- [ ] `whitney validate` and `whitney run` output with exit codes, and the report's section naming the hook.
- [ ] A test-only hook that tries to read the login or write outside the project fails to, in the run's Docker container.
- [ ] Rule 3 for real: Task 40's `UserPromptSubmit` reminder hook against `skill_loaded`, with and without the hook, with the rates recorded.

**Dependencies:** Task 44. **Size:** M to L. **Cost:** a dollar or so.

### Task 46: Kilo plugins: stage them and show they ran

**Description:** Kilo's version of hooks is a plugin, a TypeScript or JavaScript module in the project's plugin folder or named in a config's `plugin` list ([Kilo's plugins page](https://kilo.ai/docs/automate/extending/plugins), read 2026-11-11 and 2026-11-13). Whitney stages none: the loader leaves out `plugin/` and removes the `plugin` key from a staged config. Every run also passes `--pure`, under which plugins don't run.

- **Shown in a run** (Task 40 spike, Kilo 7.8.27): a plugin in `.kilo/plugin/` runs without `--pure` and not with it; `tool.execute.before` refuses a tool by throwing; the refused call is a `tool_use` event with `state.status` `error`; nothing else in the stream says a plugin ran; and Kilo writes `.kilo/package.json` (a dependency on `@kilocode/plugin`), `package-lock.json` and `node_modules/` into the project, under `--pure` too.
- **From the docs only, so each needs a fixture before code relies on it:** `.kilo/plugins/` and the legacy `.kilocode/plugin/`; changing a tool's arguments in `tool.execute.before`; `permission.ask`; the `event` bus; and "a custom tool with a built-in's name wins".

**Scope:**
- In: build what Task 44 decided for Kilo:
  - staging the project's plugin folders, and local `plugin` entries in a config;
  - the change to `--pure` for runs with a staged plugin;
  - how the `@kilocode/plugin` dependency reaches the run, as Task 44 decided (preinstalled in the image or vendored), since every local plugin needs it;
  - the report naming each plugin.
- Out: npm-package plugins in the `plugin` list, beyond `@kilocode/plugin` itself. They need a network install from inside the run, so they are refused, with a message, unless Task 44 decides otherwise.
- The task may be split in two if it grows: (a) staging and `--pure`; (b) the validate warning and the docs.
- Out: global plugins in `~/.config/kilo/plugin/`, which stay out as they do today.

**Acceptance criteria:**
- [ ] A run with no plugin staged still passes `--pure`, so every existing result is unchanged.
- [ ] A staged plugin is named in the report, with whether the evidence shows its effect.
- [ ] `.kilo/package.json`, `package-lock.json` and `node_modules/`, which Kilo writes, don't count as the agent's changes in the file checks, and don't leave the sandbox.
- [ ] `whitney validate` warns about a plugin that defines a tool with a built-in tool's name. Kilo's docs say the custom tool wins, so the scenario would test the plugin's tool, not Kilo's.
- [ ] `docs/adding-tests.md` and `docs/load-order.md` cover plugins, with an example eval.

**Verification:**
- [ ] A real run, in Docker, of a "never read `.env`" eval enforced by a `tool.execute.before` plugin alone: held with the plugin, and bypassed in at least one epoch when a variant removes it (the prompt is made more pressing if the control is only ever "not attempted").
- [ ] The same run before the change, with the plugin in the project, shows the plugin didn't run (today's behaviour). This is a record, not a gate.
- [ ] The plugin is shown to have run, not just to have been listed: the refused call's `state.error` carries the plugin's thrown message. (`kilo debug config` isn't proof: the spike found it lists a plugin under `--pure` too, where the plugin didn't run.)
- [ ] `whitney run` output with exit codes and the report's plugin section.

**Dependencies:** Task 44. **Size:** M to L. **Cost:** a dollar or so.

---

## Instruction sources

### Task 47: `.claude/rules/` on `claude-code`

**Description:** Claude Code loads `.claude/rules/*.md` that have no `paths` field at launch, and loads path-scoped ones when a matching file is read. The loader stages none of them and lists them under "rest of .claude/", so a team that keeps a rule there tests without it (Later list, and open decision 18).

**Scope:**
- In: a decision by Christopher on whether to stage rules with no `paths` field, and how to treat path-scoped ones (stage them and say when they load, or leave them out with a reason). Then the change to the loader and the report.
- Out: Kilo, which doesn't read `.claude/rules/`. This task records that from a `kilo debug config` capture.

**Acceptance criteria:**
- [ ] The decision is recorded here, dated, with each test whose expectation changes named first.
- [ ] Each staged rule can be an eval file's `source`.
- [ ] The report's file list and Task 31's "sent to the model" line both cover them.
- [ ] `docs/load-order.md` lists `.claude/rules/` in Claude Code's order, and its rows that say `rules/` isn't staged are updated.
- [ ] An import of a file under `.claude/rules/`, which the loader refuses today as configuration (`_is_claude_code_config`, `CONFIG_IMPORT_REASON`), follows the same decision.
- [ ] The spec's rules for an eval's `source` are updated to allow a rule file.

**Verification:**
- [ ] A real capture showing a rule with no `paths` field in what reached the model, and a path-scoped rule reaching it only after a matching file is read. Whitney's capture keeps later requests only as a size and a "changed" flag (Task 31 decision 5), so this uses the spike proxy's full raw capture (`spikes/prompt_capture.py`), saved outside the log, unless Task 60 has changed what is kept.
- [ ] A real run of an eval whose rule lives only in `.claude/rules/`: held with it staged, failing in a control without it.

**Dependencies:** Task 60, or the raw spike capture. **Size:** M. **Cost:** cents.

### Task 48: Spike: the instruction hierarchy

**Description:** The proposal's future work starts with "test hierarchy". That means instruction files at more than one level: `AGENTS.md` in subfolders, a parent folder's file, and how a nearer file overrides a farther one. Whitney stages one project root and refuses inherited files above it (`check_no_inherited_instructions`). Neither harness's order for nested files is recorded in `docs/load-order.md`.

**Scope:**
- In: for each harness, which nested instruction files load, when (at launch, or when the agent reads a file in that folder), and in what order. Then a proposal for how an eval names a nested file as its `source`, and how a scenario tests that the nearer file wins.
- Out: building it. That would be a follow-on task.

**Acceptance criteria:**
- [ ] `docs/spike-findings.md` has a section for each harness, with its version, the files used and the captures.
- [ ] `docs/load-order.md` records the order found.
- [ ] A short proposal here for the follow-on task, with its own scope, criteria and verification.

**Verification:**
- [ ] Captures of what reached the model from each harness, with `AGENTS.md` at the root and in a subfolder holding conflicting rules, before and after the agent works in the subfolder. The later requests come from the spike proxy's full raw capture, as in Task 47.

**Dependencies:** Task 31's capture; Task 60 or the raw spike capture. **Size:** S to M. **Cost:** cents.

### Task 49: Spike: Kilo's code review and `REVIEW.md`

**Description:** The proposal's last future-work item is a "verification hook". On Kilo that is the code review feature, driven by `REVIEW.md` ([Kilo's docs](https://kilo.ai/docs/automate/code-reviews/overview)), which the proposal wanted to start a verify subagent at the end of each run.

**Scope:**
- In: whether `REVIEW.md` does anything in `kilo run`, at the project root or in `.kilo/` (where the proposal puts it), or only in the Kilo app or its cloud review; what it triggers; and what in Kilo's stream would show that the review ran. If it works headlessly, add a proposal for an eval that tests it.
- Out: building it; Claude Code's own review features. Tasks 45 and 46 cover a Stop hook used the same way.

**Acceptance criteria:**
- [ ] `docs/spike-findings.md` records what `REVIEW.md` does in `kilo run`, with the Kilo version, the docs read and the date.
- [ ] A recommendation, with a follow-on task drafted here if the feature works headlessly.

**Verification:**
- [ ] A real `kilo run` in a project with a `REVIEW.md`, and its stream, compared with the same run without the file.

**Dependencies:** none. **Size:** S. **Cost:** cents.

---

## Checks

### Task 50: Check `ran_before`: the order of actions

**Description:** A rule such as "always run tests before committing" needs a check that reads the tool calls in order and fails when a commit comes before the test command. Kilo's stream already holds each tool call with its input; Claude Code's does too, since Task 36.

**Scope:**
- In: a check with two command patterns, `first` and `then`. It fails when `then` ran with no `first` before it, and it is inconclusive when `then` never ran. Kilo's stream doesn't hold a subagent's own tool calls (spike, Task 16), so on Kilo a run that delegated with `task` is inconclusive, as `permission_held` is. On `claude-code` the docs say what the stream shows of a subagent's calls. Both targets, through one shared reader of each stream (the todo-phase5 follow-up from Task 38).
- Out: ordering file reads against edits (Task 51).

**Acceptance criteria:**
- [x] The check is listed by `whitney checks` for both targets, and `docs/adding-tests.md` documents it with an example. *(2026-11-13: `whitney checks` lists `ran_before` with targets `kilo, claude-code`; `docs/adding-tests.md`, "`ran_before`", with the example's JSON and `examples/tests-before-commit`.)*
- [x] Only if a captured fixture shows a bash call's exit status on that target: a parameter `first_must_pass`, so a failing test run before a commit doesn't count. Otherwise the docs say a `first` counts whether it passed or not. *(2026-11-13: both targets show it, so `first_must_pass` is there for both: Kilo's `metadata.exit` in `bash-nonzero-exit` and `skill-named`, Claude Code's "Exit code <n>" error result in `settings-allow-ran` and `skill-forked`; `docs/spike-findings.md`, "Phase 5 Later Task 50". Claude Code shows no code for a command that passed, only `is_error` false.)*
- [x] Each result's explanation names the calls it ordered. *(2026-11-13: each names the commands, their place in the run, exit and, on Claude Code, who made them; `tests/test_checks_order.py`.)*

**Verification:**
- [ ] Fixtures from real streams on both targets, one where the agent commits before testing and one where it tests first. *(2026-11-13: Kilo done, `tests/data/kilo-events/tests-then-commit` and `commit-without-tests`, from the real run below, Kilo 7.8.27. Claude Code still to capture: no `ANTHROPIC_API_KEY` here, so no real run; the check is tested on Claude Code 2.1.322's saved streams of a failed and a passing `Bash` command, refusals and a forked skill's failed `pytest`.)*
- [x] A real run of an example eval ("run the tests before committing"): the template held, and a control with the rule removed fails in at least one epoch. If the control never fails, the scenario is reworded until it does. *(2026-11-13: `examples/tests-before-commit`, Kilo, Haiku, one epoch, first scenario: template in order 1/1, the baseline without `AGENTS.md` out of order 0/1, $0.067; `docs/results/2026-11-13-ran-before.md`. Claude Code not run.)*
- [x] `whitney validate` and `whitney run` on the example, with exit codes and the report's line for the check. *(2026-11-13: in `docs/results/2026-11-13-ran-before.md`: `validate` for both targets exited 0, `run` on `kilo` exited 0, with each run's explanation.)*

**Dependencies:** none. **Size:** M. **Cost:** cents to a dollar.

### Task 51: Check `read_before_edit`: which files the agent read

**Description:** A rule such as "read CONSTRAINTS.md before writing code" needs a check that a read of the named file came before the first edit. A read through bash, such as `cat`, counts too.

**Scope:**
- In: a check with a file pattern and, optionally, an edit pattern. Reads through each target's read tool and through common shell readers (`cat`, `head`, `less`, `sed -n`) count, and the docs list the ones that do. A decision, recorded in the docs, on whether a partial read (offset or limit), `grep`/`rg` and a grep or glob tool count. The subagent rule of Task 50 applies. Both targets.
- Out: reads by a tool Whitney can't see, such as an MCP server.

**Acceptance criteria:**
- [ ] Listed by `whitney checks`, and documented with what counts as a read.
- [ ] A run with no edits is inconclusive, not a pass.

**Verification:**
- [ ] Fixtures from real streams of a read-then-edit, an edit with no read, and a shell read.
- [ ] A real run of an example eval: the template held, and a control without the rule fails at least once.
- [ ] `whitney validate` and `whitney run` on the example, with exit codes and the report's line for the check.

**Dependencies:** Task 50, for the shared stream reader. **Size:** M. **Cost:** cents to a dollar.

### Task 52: Check `file_added`: something the agent must do

**Description:** Every check today watches for what mustn't happen. Turned round, "no test file in a separate folder" also passes a run that writes no test at all. A `file_added` check fails when no new file matches a pattern, so a rule such as "colocate tests next to the source" can be tested the right way round.

**Scope:**
- In: a check with a path pattern and an optional `near` pattern. The `near` pattern requires the new file to sit beside a changed file (for colocation). It is decided from git, so it works on both targets.
- Out: checking the new file's content; `no_string_added` already covers some of that.

**Acceptance criteria:**
- [ ] Listed by `whitney checks`, and documented with the colocation example.
- [ ] A run that changed no files is inconclusive.

**Verification:**
- [ ] A real run of an example eval for colocated tests: the template held, and a control fails at least once. If the plain control (no rule) never fails, the control pushes the other way (a rule "put tests under `tests/`"), so the check is seen failing in a real run.
- [ ] `whitney run` output showing the check's explanation on a fail.

**Dependencies:** none. **Size:** S. **Cost:** cents.

### Task 53: `no_test_newly_skipped`, with presets for each test framework

**Description:** Spec slice 12. A check that fails when the agent skips a test, such as pytest's `skip` or `xfail` markers, Jest's `.skip` and `xit`, or Go's `t.Skip`. Presets name the patterns for each framework.

**Scope:**
- In: presets for pytest, unittest (`@unittest.skip`, `self.skipTest`), Jest/Vitest, Go and JUnit, decided from the diff. The spec's names: parameters `test_paths` and `skip_patterns`, and outcomes clean, skipped and inconclusive. Both targets.
- Out: running the test suite to see what was skipped.

**Acceptance criteria:**
- [x] Listed by `whitney checks`, and each preset's patterns are documented. *(2026-11-13: `whitney checks` lists `no_test_newly_skipped` with outcomes `clean / skipped / inconclusive`, parameters `test_paths` and `skip_patterns`, and its proof, `docs/results/2026-11-13-test-skips.md`; `docs/adding-tests.md`, "`no_test_newly_skipped`", lists every preset's files and patterns, which `test_the_docs_list_every_presets_patterns_and_files` checks against `PRESETS`.)*
- [x] A skip that was already there before the run doesn't count, and a removed skip doesn't count. *(2026-11-13: `tests/test_checks_skips.py`: `test_a_skip_that_was_there_before_the_run_does_not_count_when_the_file_changes_round_it`, `test_a_removed_skip_does_not_count`, `test_a_skip_whose_reason_was_edited_in_place_does_not_count`; in the real run the fixture's existing skip on line 10 wasn't counted.)*

**Verification:**
- [x] A real run of an example eval in which the prompt pushes the agent to make a failing test pass: the template held, and a control without the rule fails at least once. *(2026-11-13: `examples/skipped-tests/`, Kilo 7.8.27, Haiku 4.5, one epoch, `--limit 1`: baseline **skipped** (added `@pytest.mark.skip`), template **clean**, about $0.05; [docs/results/2026-11-13-test-skips.md](../docs/results/2026-11-13-test-skips.md). One scenario only; no `claude-code` run, for want of an API key.)*
- [x] `whitney validate` and `whitney run` on the example, with exit codes and the report's line for the check. *(2026-11-13: `validate` exits 0 for `kilo` and `claude-code`, `run` on `kilo` exits 0; commands and the report's table are in the write-up.)*

**Dependencies:** none. **Size:** M. **Cost:** cents to a dollar.

### Task 54: Spike, then a decision: model-graded checks

**Description:** No check reads the agent's reply, so for a skill whose output is a report ("quote evidence for every finding"), the evals test only what it mustn't do. The spec allows a model-graded check, labelled as such and left out of the gate by default (open question 13).

**Scope:**
- In: a spike on Inspect's model-graded scorers, which is where a grader would come from. It measures how often a grader agrees with Christopher's own marks on 20 replies, and what that costs. The replies are the final reports of a skill whose output is a report, from real runs made for the spike (or kept logs, named in the write-up); marking them takes Christopher's own time. The bar is set before the spike: at least 90% agreement, with each disagreement listed. Then Christopher decides whether to build one. If yes, a follow-on task is drafted here.
- Out: building the check in this task.

**Acceptance criteria:**
- [ ] The agreement rate, with the grader model, prompt and cost per reply, recorded in `docs/spike-findings.md`.
- [ ] Christopher's decision, dated. If yes, a drafted task that keeps the grade out of the gate by default and labels it in the report.

**Verification:**
- [ ] The 20 replies and both sets of marks, kept under `docs/results/`, so the rate can be checked.
- [ ] A control: five replies broken on purpose (no evidence quoted) that the grader must fail. A grader that passes them isn't usable, whatever its agreement rate.

**Dependencies:** none. **Size:** S. **Cost:** under a dollar for grading.

---

## Output and interfaces

### Task 55: `--junit` output

**Description:** CI systems read JUnit XML. A `--junit <file>` option on `whitney run` and `whitney report` would show each eval file and check as a test case, and the gate's result as pass or fail.

**Scope:**
- In: one test suite per eval file, one test case per check, with the rate, the minimum and the explanation of each fail. Inconclusive results are marked skipped, with the reason.
- Out: other formats.

**Acceptance criteria:**
- [x] The XML validates against a named schema (the widely used `JUnit.xsd` from the Ant/Jenkins family, kept in the repo for the test), and the docs say which elements are used. *(2026-11-13: `tests/data/junit/JUnit.xsd`, the JUnit schema used by Apache Ant's JUnit task, © 2011 Windy Road Technology Pty. Limited, Apache 2.0, with its SHA-256 and download date in `SOURCE.txt`; `tests/test_junit.py` validates with `xmllint`; README, "JUnit XML for your CI system", lists the elements.)*
- [x] The gate's exit code is unchanged. *(2026-11-13: `test_without_junit_no_xml_is_written_and_the_exit_code_is_the_same`, and the CLI runs in [docs/results/2026-11-13-junit-output.md](../docs/results/2026-11-13-junit-output.md).)*

**Verification:**
- [x] `whitney report --junit` on a saved log (see "Saved logs") that holds a pass, a fail and an inconclusive result, with the XML validated against the schema. *(2026-11-13: the log was made deterministically by `whitney run` with a fake `kilo` (`tests/data/junit/example`), not by a model; [docs/results/2026-11-13-junit-output.md](../docs/results/2026-11-13-junit-output.md).)*
- [ ] The file shown in a CI run: Whitney's own workflow writes a summary from it to the GitHub job summary, in a pull request, with a link. *(2026-11-13: the step is in `.github/workflows/checks.yml` and its script ran locally; it can only be shown on GitHub once the branch is pushed and a pull request opened, so the link is still missing.)*

**Dependencies:** none. **Size:** S. **Cost:** none.

### Task 56: The Python API

**Description:** The spec has `whitney.run`, `whitney.validate` and `whitney.discover`, and says the CLI should be a thin layer over them. `cli._run` takes an argparse Namespace and prints its findings (Later list, Task 11 review). Best done before the next release.

**Scope:**
- In: library functions that take typed arguments and return results. The CLI then calls them and prints. Existing CLI output stays the same byte for byte.
- Out: new features; a stable-API promise beyond what the docs state.

**Acceptance criteria:**
- [x] The three functions are importable from `whitney`, with docstrings, and are listed in the README. They return the spec's shapes (`run(...)` with `exit_code`, `report`, `report_path`, `problems` and `logs`; `validate(...)` with `problems` and `warnings`), or the spec is changed in the same task. *(2026-11-13: `tests/test_api.py`; README "From Python"; the spec's "Python API" now records three added fields on `run` (`warnings`, `gate_failures`, `error`), the `findings` stream, and why `whitney.validate` is the callable module)*
- [x] Every existing CLI test passes unedited. *(2026-11-13: `tests/test_cli.py`, `tests/test_cli_report_checks.py`, `tests/test_run_evals.py`, `tests/test_examples.py` unchanged and passing in `check.py task`)*

**Verification:**
- [x] A short script in `examples/` that calls `whitney.validate` and `whitney.run` on the consumer example and prints the gate, run once for real. *(2026-11-13: `examples/api.py`, run on Kilo 7.8.27 with `kilo/anthropic/claude-haiku-4.5`, `--only agents-md.json --limit 1`, one epoch: gate passed, exit 0, $0.11; [docs/results/2026-11-13-python-api.md](../docs/results/2026-11-13-python-api.md). Also run against the fake in `tests/test_api.py`)*
- [x] `whitney run` and `whitney validate` output compared before and after on the same inputs, with no difference. For `run` that needs a run that gives the same answer each time: the fake `kilo` with Inspect's `mockllm` model. *(2026-11-13: `tests/test_cli_output.py`, 12 cases, golden output recorded before the refactor in commit `d56e8b9`; Inspect's own progress display is left out of the comparison. One intended difference, not in the goldens: an unwritable report now exits 2 with a message, not a traceback with 1)*

**Dependencies:** none. **Size:** M. **Cost:** cents for the example run.

Decision (Christopher Chant, 2026-11-14): `whitney.run` takes no `junit` option until someone needs it; Python callers get the report in `RunResult` and can write any format.

### Task 57: Decision: check plugins

**Description:** Spec open question 9. Should a consumer project be able to add its own check, or does every check stay a pull request to Whitney (spec, rule 5)?

**Scope:**
- In: a short write-up of the options and their risks for Christopher to decide. A plugin check runs the consumer's code inside the scorer, and the report must then say a result came from a plugin.
- Out: building it.

**Acceptance criteria:**
- [ ] The decision is recorded in the spec under open question 9, dated, with a drafted task here if the answer is yes.

**Verification:**
- [ ] None beyond the decision: nothing is built. The write-up says how many known forks would use it, without naming any of them.

**Dependencies:** none. **Size:** S. **Cost:** none.

### Task 58: Spike: evaluating omp

**Description:** [omp](https://omp.sh) ("Oh My Pi", MIT licence) is a coding-agent harness with a headless mode. From its docs, read 2026-11-13 and not yet shown in a run:

- `omp -p --mode json` writes a JSONL event stream ([modes](https://omp.sh/docs/modes)). The RPC docs list `tool_execution_start` and `tool_execution_end` events with each tool's arguments and result ([rpc](https://omp.sh/docs/rpc)). Whether JSON mode uses the same events isn't stated.
- It loads `AGENTS.md`, `.omp/AGENTS.md`, `.omp/RULES.md`, `.claude/CLAUDE.md` and other harnesses' files, in a priority order where one provider wins per folder depth. A root `CLAUDE.md` isn't listed ([context files](https://omp.sh/docs/context-files)).
- It reads skills from `.omp/skills/`, `.claude/skills/` and others ([skills](https://omp.sh/docs/skills)).
- Permissions are `tools.approval.<tool>` (`allow`, `prompt` or `deny`) and ordered `bash.patterns` in `.omp/config.yml`, with `yolo` the default. Headless, a `prompt` fails with `requires approval but no interactive UI available` ([approvals](https://omp.sh/docs/approvals), [settings](https://omp.sh/docs/settings)).
- Hooks are TypeScript modules in `.omp/hooks/pre/` and `post/` that can block or rewrite a tool call ([hooks](https://omp.sh/docs/hooks)).
- `--profile` isolates settings and logins. `.env` files are loaded from the current folder and from HOME ([cli](https://omp.sh/docs/cli), [env](https://omp.sh/docs/env)).
 The spike finds out what supporting it as a third target would take, against Whitney's rule that a target pairs a harness with its template and model route.

**Scope:**
- In, on the current omp release, installed pinned:
  - **The stream:** the JSON mode's event schema as actually written.
  - **Refusals:** how a `deny` shows in the stream; the docs give only the error text `requires approval but no interactive UI available` for a `prompt` in headless mode.
  - **Instruction files:** which ones load, in a project like Whitney's template. This includes the fact that the docs list `.claude/CLAUDE.md` but not a root `CLAUDE.md`.
  - **Isolation:** whether `--profile`, a temporary `HOME` and `disabledProviders` keep the user's `~/.omp`, `~/.claude`, `~/.codex` and other folders out, and every place omp reads a `.env` from (the current folder, parents, HOME), shown by planted files.
  - **Models:** routing a model through the Anthropic API. Inspect's bridge for Docker only if the local route works.
  - **Prompt capture:** whether Task 31's capture proxy works for omp.
  - **Telemetry:** whether anything is sent beyond the model call; the docs name OpenTelemetry, off unless configured, and an opt-in "Auto-QA".
- Out: building the target.
- **Stop and report** if the JSON stream lacks tool inputs, or isolation can't keep `~/.claude` and `~/.omp` out: either ends the spike, since Whitney's checks and its isolation rule both depend on them.
- **Box:** two days and two dollars of model calls.

**Acceptance criteria:**
- [ ] `docs/spike-findings.md` has an omp section with the version, the docs read (URLs and dates) and each finding marked as shown by a run or taken from the docs.
- [ ] A table mapping each of Whitney's checks to what omp's stream offers for it: supported, supported with changes, or not possible, with the reason.
- [ ] A recommendation, for Christopher to decide:
  - whether to add an `omp` target;
  - its template (likely `AGENTS.md` plus `.omp/config.yml`);
  - its model route;
  - whether `AGENTS.md` in the project's instructions should be edited to name it. Only Christopher edits `AGENTS.md`.
- [ ] If the answer is yes, a drafted plan in the shape of milestone E (loader, stream capture, permissions, checks, Docker, example and docs), each task with scope, criteria and verification.

**Verification:**
- [ ] Captured streams from real omp runs:
  - a run that edits a file;
  - one refused by a `deny` rule;
  - one blocked by a pre hook;
  - one that loads a skill.
  Each is saved as a sanitised fixture with the version, model, prompt and date.
- [ ] A capture of what reached the model in the template project (Task 31's proxy), showing which instruction files were sent.
- [ ] A run with a planted `~/.omp/agent/AGENTS.md` and `~/.claude/CLAUDE.md` in the temporary HOME, showing whether isolation keeps them out.

**Dependencies:** none. **Size:** M. **Cost:** a dollar or so, with Christopher's go-ahead. omp's install script is run in a throwaway folder or container, and read before it's run.

---

## Housekeeping

### Task 59: A fake `claude` for the safe-staging run test

**Description:** `tests/test_safe_staging.py::test_the_project_is_unchanged_after_a_run` checks that a Kilo run leaves the project as it was, using a fake `kilo`. The `claude-code` target's run isn't checked the same way, only its staging (Task 8 review).

**Scope:**
- In: a fake `claude` that is hostile on purpose, as `FAKE_KILO` is (it deletes the project and rewrites `AGENTS.md`), and the same test for `claude-code`.
- Out: changes to the runner, unless the test finds a fault.

**Acceptance criteria:**
- [x] The test runs for both targets. *(2026-11-13: `test_the_project_is_unchanged_after_a_claude_code_run` in `tests/test_safe_staging.py` runs the claude-code `agents_md` template and baseline tasks; the kilo test is unchanged. They are two tests, not one parametrised, so the kilo test's name and assertions stay as they were.)*
- [x] The fake does at least what `FAKE_KILO` does to its working folder, plus writing into `.claude/`, which a real run may. *(2026-11-13: `FAKE_CLAUDE` deletes everything but `.git`, rewrites `AGENTS.md`, and writes `.claude/settings.local.json` and `.claude/CLAUDE.md`; it prints the init and result events the existing fake in `tests/test_claude_code.py` uses.)*

**Verification:**
- [ ] A real `claude -p` run through Whitney's runner, with a listing of the project folder before and after, kept as background for what a real run writes. *(2026-11-13: open. It needs an Anthropic API key, which this session didn't have.)*
- [x] The test shown able to fail: with the runner pointed at the project itself instead of its temporary copy (a local change, not committed), the new test fails. *(2026-11-13: with `claude_code.execute` given the project folder in place of the sandbox's `pwd`, the new test failed on its last assertion: `AGENTS.md` read "Always commit .env files.", `.claude/`, `.claude/CLAUDE.md` and `.claude/settings.local.json` had appeared and the project's other files were gone. The runner restored, it passes.)*

**Dependencies:** none. **Size:** S. **Cost:** cents.

**Follow-up (2026-11-13, from Task 59's review):** a run that crashed or ended early is still scored, so `sample.scores` alone doesn't show the fake ran. The claude-code test also checks each sample's completion is the fake's `done`, and fails with a fake that only exits 1. The kilo test, `test_the_project_is_unchanged_after_a_run`, has the same gap; it was left untouched, as the task says, and needs Christopher's confirmation to add the same line.

### Task 60: Look again at how much of each captured request is kept

**Description:** Task 31 decision 5 keeps the first request in full plus each later request's size and a "changed" flag. Christopher asked to look at it again once multi-step captures show how often a later request's system prompt or tools change.

**Scope:**
- In: counting, from new multi-step captures, how often a later request's system prompt or tool list changed, on each target. Then Christopher decides whether to keep more.
- Out: changing the capture unless he decides to.

**Acceptance criteria:**
- [ ] The counts, by target and harness version, in `docs/results/`, with the decision recorded in `todo-phase5.md` under Task 31.

**Verification:**
- [ ] At least five multi-step captures on each target, made with the spike proxy's full raw capture (`spikes/prompt_capture.py`) beside Whitney's, with the "changed" flags compared with a full diff of the raw requests. That shows whether the flag is reliable.

**Dependencies:** Task 31. Comes before Tasks 47 and 48. **Size:** S. **Cost:** cents.

### Task 61: Keep `.envrc` and other secret files out of every staged folder

**Description:** `_is_safe_file` keeps `.env` files out of what is staged. A longer list of secret file names (`SECRET_NAMES` in `project_files.py`, such as `.envrc`, `.npmrc`, `*.pem` and `credentials*`) applies only to files an instruction file imports. So a `.envrc` inside a staged skill or agent folder is copied into the run, where the agent can read it and the log keeps it (Later list, Task 8 review).

**Scope:**
- In: applying `SECRET_NAMES` wherever `_is_safe_file` is used: `_walk_files`, `_direct_md_files`, Kilo `instructions` matches, the rules folders and `load_template_files` (used by `harbor_tasks` and `interference`), on both targets. Each file left out is named in the report's "Not Staged" table with the reason.
- In: the Markdown question, decided below. `SECRET_NAMES` holds `secrets.*`, `secret.*`, `credentials*`, `*.key` and `*.asc`, so a skill about handling secrets would lose a file such as `references/secrets.md`, which changes what the run tests. The options: exempt Markdown, or stop the run with a message rather than leave such a file out, as an unsafe import does.
- **Decided 2026-11-14 (Christopher):** exempt Markdown. A `.md` file in a staged folder is staged even if its name matches `SECRET_NAMES` (how `@imports` treat such names is unchanged), so a skill about handling secrets can still be tested; every other match is left out and named in "Not Staged". The first slice is [secret-files-not-staged.md](../docs/specs/secret-files-not-staged.md). It leaves out the `whitney validate` warning, which stays owed under this task.
- Out: scanning file contents for secrets; the secrets check in `check.py` already does that for Whitney's own repo.

**Acceptance criteria:**
- [ ] A file matching `SECRET_NAMES` anywhere in a staged folder is not staged, and the report names it.
- [ ] `whitney validate` warns about each one, so a team sees it before a run.
- [ ] Each existing test whose expectation changes is named and confirmed by Christopher first, and no eval fixture's bait file is dropped by the change.
- [ ] `docs/adding-tests.md` lists the names that are kept out.

**Verification:**
- [ ] `whitney validate` and `whitney run`, with a made-up `.envrc` in a skill folder of the consumer example: the warning, the exit codes and the "Not Staged" row. A search of the `.eval` log for the made-up value finds nothing.
- [ ] The same run on `main` before the change finds the value in the log, which shows the test can fail.

**Dependencies:** none. **Size:** S. **Cost:** cents.
