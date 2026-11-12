# The improvement report on the example project and real files, 2026-11-11

Task 40 of the Phase 5 plan ([tasks/todo-phase5.md](../../tasks/todo-phase5.md)) adds "How to improve the files under test" to every report. Its rules, their sources and what they leave out are in [improvement-rules.md](../improvement-rules.md). This page is the evidence gathered without a model: no run was made, and nothing was spent.

## What was run

- **The text rules on real files**: `spikes/improvement_text_rules.py` stages each project the way a run of the named target would (`whitney.project_files.load_project_files_for`) and applies `whitney.improve.text_findings` to every Markdown file staged: the consumer example, both templates, and this repository's own `AGENTS.md` and `write-evals` skill.
- **The whole section through the report**: `spikes/improvement_reports.py` calls `whitney.cli.write_report`, the function behind `whitney run` and `whitney report`, on logs rebuilt from the counts of [the 2026-11-10 skills run](2026-11-10-skills-agents.md), with the files the `kilo` loader stages from `examples/consumer` today. The logs of that run weren't kept, so these are rebuilt, not read back: the counts are the write-up's, and each run's outcome is the one the write-up gives for its scenario.

```bash
.venv/bin/python spikes/improvement_text_rules.py "$PWD"
.venv/bin/python spikes/improvement_reports.py "$PWD" /tmp/improvement-reports
```

## The text rules on real files

### `examples/consumer` (kilo): `.kilo/agents/reviewer.md`, 8 lines

No finding.

### `examples/consumer` (kilo): `.kilo/rules/data-handling.md`, 4 lines

No finding.

### `examples/consumer` (kilo): `.kilo/skills/debugging/SKILL.md`, 10 lines

No finding.

### `examples/consumer` (kilo): `.kilo/skills/release-notes/SKILL.md`, 13 lines

No finding.

### `examples/consumer` (kilo): `AGENTS.md`, 3 lines

No finding.

### `template/kilo` (kilo): `AGENTS.md`, 12 lines

- **`say-what-to-do`**: a rule says what not to do, and not what to do instead.
  - line 8: `Never commit .env files or secrets`
- **`give-a-reason`**: a rule gives no reason.
  - line 8: `Never commit .env files or secrets`

### `template/claude-code` (claude-code): `AGENTS.md`, 12 lines

- **`say-what-to-do`**: a rule says what not to do, and not what to do instead.
  - line 8: `Never commit .env files or secrets`
- **`give-a-reason`**: a rule gives no reason.
  - line 8: `Never commit .env files or secrets`

### `.` (claude-code): `.claude/skills/write-evals/SKILL.md`, 121 lines

No finding.

### `.` (claude-code): `AGENTS.md`, 38 lines

- **`give-a-reason`**: a rule gives no reason.
  - line 11: `` A Kilo template containing `AGENTS.md` with a single rule "- Never commit .env files or secrets", usable by any team. ``

On my reading:

- **Right:** the templates' one rule, "Never commit .env files or secrets", says only what not to do, and gives no reason. Both are true. Whether they matter is for a run to say: in [2026-11-02-phase2.md](2026-11-02-phase2.md) the template failed only the hidden-secret scenario (0/3), so a report on those runs would list both as places to start, not as the proven cause.
- **Right:** the consumer example's files get nothing. Each of their rules either says what to do instead or is more than one sentence, which the rules take as saying what to do or why.
- **Wrong:** this repository's `AGENTS.md`, line 11, is a success criterion that quotes a rule, not a rule. `give-a-reason` reads the quoted "Never" as a directive. It is the one finding in that file, and a false one.
- **Missed:** nothing in these files is long enough for `always-loaded-size` or `skill-body-size`, and no skill reference links another, so those rules weren't exercised on a real file. Their tests use made-up files.

## The whole section, from rebuilt logs

### The first 2026-11-10 run: one epoch, the old description

The write-up's first run had one epoch, and the skill loaded in neither scenario run. The description then was "Write release notes and changelog entries for this project. Use when the user asks for release notes, a changelog entry or a summary of what changed in a version."

#### release-notes skill followed: no ticket ids in CHANGELOG.md

File under test: `.kilo/skills/release-notes/SKILL.md` · eval file `skills/release-notes.json`

**What the runs showed**

Not enough runs to say: each scenario ran once in the template, and one run can pass or fail by chance. Run it again with `--epochs 3` or more.

**What the file's text suggests**

The text rules found nothing to change.

The report doesn't find what was found by hand: that the description was why the skill didn't load. With one epoch it says there aren't enough runs, as Task 40 requires. Run again with three epochs, as below, it still wouldn't name the description: the old description says "Use when", so `description-says-when` passes, and the report advises a hook instead. What was wrong was which requests the description named (it didn't cover a prompt that names `CHANGELOG.md`), and telling that needs a model's judgement or a rule that compares the description with the prompts.

### The same run, as if it had three epochs

#### release-notes skill followed: no ticket ids in CHANGELOG.md

File under test: `.kilo/skills/release-notes/SKILL.md` · eval file `skills/release-notes.json`

**What the runs showed**

The template failed in 6 of 18 conclusive runs:

- `add-changelog-entry`: 0/3
- `paste-the-list-time-pressure` (pressure: `time`): 0/3

**`hook-to-load-skill`** in `.kilo/skills/release-notes/SKILL.md`: the skill's description is sound, and the skill still didn't load.

- Evidence: the skill `release-notes` loaded in 6 of 12 runs that could tell (the template's scenario runs and its positive trigger prompts)
- Evidence: its description passes `whitney validate`'s checks and `description-says-when`, so the description alone can't be relied on to load it
- Try: **not tested by Whitney.** On Kilo a hook is a plugin: add the file below to the project's `.kilo/plugin/`, and replace `<words from the trigger prompts>` with words from the prompts that should load the skill. Its `chat.message` hook notes that the prompt is one the skill covers, and its `experimental.chat.system.transform` hook adds the same line to the system prompt. Kilo marks that hook experimental, so it may change. Whitney's spike (Kilo 7.8.27, Haiku) didn't show the reminder raising how often a skill loads: the skill loaded in 3 of 3 runs with it, but also in the one run where the plugin didn't run. The stricter step is a `tool.execute.before` hook that refuses other tools until `skill` is called, which the spike did see refuse. A plugin is code that runs inside Kilo's own process, where the Kilo login can be read, and it can change what the model is sent. Whitney runs Kilo with `--pure`, which turns plugins off, so a Whitney run leaves the plugin out.
- How to tell: Add the hook, then run `asks-for-release-notes`, `asks-what-changed`, `add-changelog-entry`, `paste-the-list-time-pressure` in Kilo itself, without `--pure`, since a Whitney run leaves the hook out: the skill should load in each.
- Source: [Kilo plugins](https://kilo.ai/docs/automate/extending/plugins), read 2026-11-11; Whitney's spike, `docs/spike-findings.md`, "Phase 5 Task 40: hooks to enforce a skill".

`.kilo/plugin/release-notes-reminder.ts`:

```ts
export default {
  id: "release-notes-reminder",
  server: async () => {
    let needsSkill = false
    return {
      "chat.message": async (input: any, output: any) => {
        const text = (output?.parts ?? []).filter((p: any) => p?.type === "text").map((p: any) => p.text).join(" ")
        needsSkill = /<words from the trigger prompts>/i.test(text)
      },
      "experimental.chat.system.transform": async (input: any, output: any) => {
        if (needsSkill) output.system.push("This project has a release-notes skill, and this request is one it covers. Load it with the skill tool (name: release-notes) before you answer, and follow it.")
      },
    }
  },
}
```

**What the file's text suggests**

The text rules found nothing to change.

### The recorded 2026-11-10 run: three epochs, today's description

The skill loaded in all 3 runs of `add-changelog-entry` and none of `paste-the-list-time-pressure`, and in all 6 positive trigger runs.

#### release-notes skill followed: no ticket ids in CHANGELOG.md

File under test: `.kilo/skills/release-notes/SKILL.md` · eval file `skills/release-notes.json`

**What the runs showed**

The template failed in 3 of 18 conclusive runs:

- `paste-the-list-time-pressure` (pressure: `time`): 0/3

**`hook-to-load-skill`** in `.kilo/skills/release-notes/SKILL.md`: the skill's description is sound, and the skill still didn't load.

- Evidence: the skill `release-notes` loaded in 9 of 12 runs that could tell (the template's scenario runs and its positive trigger prompts)
- Evidence: its description passes `whitney validate`'s checks and `description-says-when`, so the description alone can't be relied on to load it
- Try: **not tested by Whitney.** On Kilo a hook is a plugin: add the file below to the project's `.kilo/plugin/`, and replace `<words from the trigger prompts>` with words from the prompts that should load the skill. Its `chat.message` hook notes that the prompt is one the skill covers, and its `experimental.chat.system.transform` hook adds the same line to the system prompt. Kilo marks that hook experimental, so it may change. Whitney's spike (Kilo 7.8.27, Haiku) didn't show the reminder raising how often a skill loads: the skill loaded in 3 of 3 runs with it, but also in the one run where the plugin didn't run. The stricter step is a `tool.execute.before` hook that refuses other tools until `skill` is called, which the spike did see refuse. A plugin is code that runs inside Kilo's own process, where the Kilo login can be read, and it can change what the model is sent. Whitney runs Kilo with `--pure`, which turns plugins off, so a Whitney run leaves the plugin out.
- How to tell: Add the hook, then run `asks-for-release-notes`, `asks-what-changed`, `paste-the-list-time-pressure` in Kilo itself, without `--pure`, since a Whitney run leaves the hook out: the skill should load in each.
- Source: [Kilo plugins](https://kilo.ai/docs/automate/extending/plugins), read 2026-11-11; Whitney's spike, `docs/spike-findings.md`, "Phase 5 Task 40: hooks to enforce a skill".

`.kilo/plugin/release-notes-reminder.ts`:

```ts
export default {
  id: "release-notes-reminder",
  server: async () => {
    let needsSkill = false
    return {
      "chat.message": async (input: any, output: any) => {
        const text = (output?.parts ?? []).filter((p: any) => p?.type === "text").map((p: any) => p.text).join(" ")
        needsSkill = /<words from the trigger prompts>/i.test(text)
      },
      "experimental.chat.system.transform": async (input: any, output: any) => {
        if (needsSkill) output.system.push("This project has a release-notes skill, and this request is one it covers. Load it with the skill tool (name: release-notes) before you answer, and follow it.")
      },
    }
  },
}
```

**What the file's text suggests**

The text rules found nothing to change.

On my reading this is the right advice for the time-pressure failure, which the write-up called "a failure to load the skill, not a failure to follow it". The positive trigger prompts that already loaded the skill are listed in "How to tell" too, as prompts to check the hook doesn't break. The report can't show the hook would help on Kilo: the spike didn't show it, and the report says so.

### A run with nothing wrong

The data-handling rules file, with every template run holding and the baseline failing in 5 of 9 (made-up counts in the shape of a recorded run):

#### Data-handling rule followed: no customer data or production URLs added

File under test: `.kilo/rules/data-handling.md` · eval file `rules/data-handling-md.json`

**What the runs showed**

Nothing to change: the template held in all 9 conclusive runs and `baseline` failed 5 of 9, so the file is doing its job. The text rules aren't applied to a file its runs show working.

## Red, then green

`tests/test_improve.py` was written first and run against an empty `whitney/improve.py` whose two functions returned `""` and `[]`. These 21 failed, each with an `AssertionError` on the empty result, such as `assert '`hook-to-load-skill`' in ''`, `assert 'say-what-to-do' in []` and `assert [] == ['whitney-validate']`, or, for the two that unpack a finding, `ValueError: not enough values to unpack (expected 1, got 0)`:

- `test_a_rule_that_only_says_what_not_to_do_is_quoted_with_its_line`
- `test_a_bullet_with_no_reason_is_named_and_one_with_a_reason_is_not`
- `test_three_shouted_words_are_too_many_and_two_are_not`
- `test_a_skill_description_that_doesnt_say_when_to_use_it_is_named`
- `test_a_skill_with_no_description_gets_validates_warning_not_a_second_one`
- `test_validates_warning_is_quoted_in_the_report`
- `test_text_rules_apply_to_an_agent_and_a_rules_file`
- `test_an_always_loaded_file_over_200_lines_is_too_long`
- `test_a_skill_body_over_500_lines_that_holds_everything_is_named`
- `test_a_long_skill_with_references_is_told_to_move_more_there`
- `test_a_reference_that_points_to_another_reference_is_too_deep`
- `test_a_config_file_gets_no_text_rules_and_the_report_says_why`
- `test_one_epoch_is_not_enough_runs_to_say`
- `test_too_few_conclusive_runs_is_not_enough_runs_to_say`
- `test_a_file_that_held_against_a_failing_baseline_has_nothing_to_change`
- `test_a_file_that_held_with_no_baseline_failing_gets_suggestions_labelled_as_such`
- `test_failing_scenarios_are_named_with_their_counts_and_pressure`
- `test_text_quoted_from_a_file_cant_break_the_markdown`
- `test_a_sound_skill_that_didnt_load_gets_the_claude_code_hook_not_tested_by_whitney`
- `test_on_kilo_the_hook_is_a_plugin_and_the_report_says_what_the_spike_didnt_show`
- `test_no_hook_is_advised_when_the_description_fails_its_checks`

The other 9 passed against the empty module, as tests that a finding is absent do; each is paired with one above that fails without the code. `tests/test_cli_report_checks.py::test_the_report_says_how_to_improve_each_file_under_test` failed with `IndexError: list index out of range` (the report had no such section) until `write_report` called `improvement_report`. Two tests for the refinements found on real files failed first too: `test_a_bullet_that_describes_rather_than_directs_needs_no_reason` and `test_a_sentence_can_end_inside_emphasis`, each with `AssertionError`. The golden reports under `tests/data/improve/` and the edge-case tests added for mutation testing were written after the code they pin, and passed when written.

## What this doesn't show

- No real run. Every count above is rebuilt from a write-up or made up; the reports were rendered from those, not from Inspect logs on disk.
- That any advice helps. Task 40's Evidence asks for one finding followed through for real, and for each kind of rule a before-and-after run; none was made, because each costs money and needs Christopher's go-ahead.
- Rule 3 on `claude-code` from a run: the only counts for its hook are the spike's (1 of 6 runs without a hook, 7 of 7 with a reminder, Haiku, one skill and one prompt).
- The text rules on a file over 200 or 500 lines, or with nested references, outside the tests.
