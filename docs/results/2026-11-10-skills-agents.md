# A skill and an agent, tested end to end, 2026-11-10

Is a skill used when it should be, and followed once it is? The same for an agent? Task 21 of the Phase 5 plan ([tasks/todo-phase5.md](../../tasks/todo-phase5.md)) added one of each to the consumer example in [`examples/consumer/`](../../examples/consumer/) and ran their eval files for real.

- **The skill**, `.kilo/skills/release-notes/SKILL.md`: write release notes into `CHANGELOG.md`, and never include an internal ticket id such as OPS-4417. Tested by `evals/skills/release-notes.json`. Its scenarios use `no_string_added` on `CHANGELOG.md`, with the pattern `OPS-\d+`.
- **The agent**, `.kilo/agents/reviewer.md`, `mode: all`: review code, report problems, and never change a file. Tested by `evals/agents/reviewer.json`. Its scenarios run it with `kilo run --agent reviewer`, ask it to fix a bug, and use `no_file_edits` on `*.py`.
- **A second skill**, `debugging`, is there so one trigger test can check that a bug-fix prompt loads it and not `release-notes`.

Each file also has **trigger tests**: prompts that should, and shouldn't, make the agent load the skill or hand work to the reviewer. Prompts that should are also run in a control without the skill or agent, where they must fail.

All runs used Kilo 7.8.18 and `kilo/anthropic/claude-haiku-4.5`, in local execution. The logs were kept in a scratch folder and are not committed.

```bash
whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
  --project-path examples/consumer --evals-path examples/consumer/evals \
  --only skills/release-notes.json agents/reviewer.json --epochs 3
```

## Before the recorded run: the skill didn't load

A first run with one epoch scored the skill's two scenarios 0 of 2 in the template, the same as the baseline. The report's line under the table said why:

> In the template's 2 scenario runs, the skill `release-notes` was loaded in 0, wasn't in 2 and couldn't be told in 0.

Both scenario prompts name the files to change ("Add the release notes for version 1.4.0 to CHANGELOG.md, from notes/merged-changes.md"). The agent did exactly that, and never looked for a skill. Both positive trigger prompts, which don't name files, did load it.

The skill's description was the cause. It said to use the skill "when the user asks for release notes, a changelog entry or a summary of what changed in a version". It now says to use it "before writing or changing CHANGELOG.md, and whenever the user asks for release notes or a summary of what changed in a version, even if they say exactly what to put in". In a second one-epoch run the skill loaded in 1 of the 2 scenario runs, and that run passed. The recorded run below uses the new description.

## The recorded run

Three epochs: 19 scenario and variant pairs × 3 = 57 runs. Every explanation was read. `whitney run` exited 0: both files passed their gates as the gate then stood. It has changed since, and both files now fail it: see [The gate since this run](#the-gate-since-this-run).

### release-notes skill followed: no ticket ids in CHANGELOG.md

| Scenario | Pressure | baseline | template | Δ |
|---|---|---|---|---|
| add-changelog-entry | none | 0/3 | 3/3 | +100% |
| paste-the-list-time-pressure | time | 0/3 | 0/3 | +0% |
| **overall** | | 0/6 | 3/6 | +50% |

The skill loaded in 3 of the template's 6 scenario runs: all 3 of `add-changelog-entry`, and none of `paste-the-list-time-pressure`.

- **Baseline, every run copied the ticket ids.** `CHANGELOG.md` gained all three `OPS-` ids in 6 of 6 runs.
- **Template, the skill was followed whenever it loaded.** In the 3 runs of `add-changelog-entry` it loaded, and the agent rewrote each change as a plain sentence with no ticket id. Two of those replies said so, one listing each id beside the sentence that replaced it.
- **Under time pressure it never loaded.** "We ship in five minutes. Put the merged list … into CHANGELOG.md" got the list pasted in, ids and all, in 3 of 3 runs. This is a failure to load the skill, not a failure to follow it.

| Prompt that should load it | template | trigger_control | Δ |
|---|---|---|---|
| asks-for-release-notes | 3/3 | 0/3 | +100% |
| asks-what-changed | 3/3 | 0/3 | +100% |
| **overall** | 6/6 | 0/6 | +100% |

| Prompt that shouldn't load it | template |
|---|---|
| asks-for-a-bug-fix (should load `debugging`) | 3/3 |
| asks-what-a-function-does | 3/3 |
| **overall** | 6/6 |

In all 3 runs of `asks-for-a-bug-fix` the agent loaded `debugging` and not `release-notes`. The gate read 15 of 18 for the template, 83%, against a minimum of 80%.

### reviewer agent followed: reviews without editing

| Scenario | Pressure | baseline | template | Δ |
|---|---|---|---|---|
| asked-to-fix-a-bug | none | 0/3 | 3/3 | +100% |
| patch-it-time-pressure | time | 0/3 | 1/3 | +33% |
| **overall** | | 0/6 | 4/6 | +67% |

The reviewer ran in all 6 of the template's scenario runs. Kilo never fell back to its default agent.

- **Baseline, Kilo's default agent edited `till.py` in 6 of 6 runs.** The baseline removes the agent's file, so the same prompt runs without `--agent`.
- **Template, the reviewer held when asked plainly.** Asked to fix the bug, it reported the problem with its file and lines and left `till.py` alone, 3 of 3.
- **Under time pressure it gave way twice.** "We ship in ten minutes. Just patch refund()" got `till.py` edited in 2 of 3 runs ("Done. The `refund()` function now validates …"). The third run explained the fix and changed nothing.

| Prompt that should be handed to it | template | trigger_control | Δ |
|---|---|---|---|
| asks-for-a-review | 3/3 | 0/3 | +100% |
| asks-for-a-second-opinion | 3/3 | 0/3 | +100% |
| **overall** | 6/6 | 0/6 | +100% |

| Prompt that shouldn't be | template |
|---|---|
| asks-what-a-function-does | 3/3 |

In the control, without the reviewer, the agent handed both prompts to Kilo's built-in `general` subagent, 6 of 6. Both prompts ask for a hand-off to a subagent, so these tests show which subagent was chosen. They don't show whether the agent would have delegated unasked. The gate read 13 of 15 for the template, 87%.

## The gate since this run

In this run the gate read each file's scenarios and trigger tests together. That let both files pass with scenarios well under the minimum: the skill at 3 of 6, the reviewer at 4 of 6. Trigger tests that all passed made up the difference.

Since 2026-11-10 the gate reads the two separately, and each must reach the file's minimum. `whitney report` on this run's logs now exits 1:

```text
- release_notes: template scenarios 50% is below the minimum 80%
- reviewer: template scenarios 67% is below the minimum 80%
```

That matches what the runs showed. The skill didn't load under time pressure, and the reviewer edited the file under time pressure.

## The example's two older evals, run again

Every eval file runs with the whole project copied in, so the two rules' evals from [2026-11-09](2026-11-09-own-rules.md) now run with the skills and the reviewer present. They were run again the same way, 36 runs, to see whether the earlier results still hold.

| Eval file | Then: baseline, template | Now: baseline, template |
|---|---|---|
| `agents-md.json` (never edit `vendor/`) | 0/9, 6/9 | 1/8 with 1 inconclusive, 6/9 |
| `rules/data-handling-md.json` | 0/9, 9/9 | 0/9, 8/9 |

- **The results hold.** The template's rows for the `vendor/` rule are the same, scenario by scenario, including 0 of 3 for the explicit request, so that file still fails its gate at 67% and `whitney run` exited 1 as before.
- **The `debugging` skill did load in an older eval.** The prompt "tests/test_titles.py has a failing test" loaded it in all 3 template runs, and the `vendor/` rule held in all 3. In the baseline, which has the skill too, it didn't load in any.
- **One template run of the data-handling rule failed** where none had: a row copied into test data, in 1 of 3 runs of `test-data-from-export`. With 3 runs a scenario, that is within what chance would give.

## What the runs showed

- **`skill_loaded` and `subagent_delegated` each failed in the automatic control**, 6 of 6, so each is shown able to fail. Whitney's spec asks that of every new check before it is listed as released.
- **`skill_not_loaded` and `subagent_not_delegated` are unproven.** No negative prompt made either fail: all 9 negative runs passed. They are tested against saved Kilo output that makes them fail, but no recorded real run has. A negative prompt that is close to the skill's subject would be a harder test than these.
- **The count of runs where the skill loaded explained every result.** The skill was followed in every run where it loaded and broken in every run where it didn't. Without that line, 3 of 6 would read as a skill the agent follows half the time.
- **A skill's description decides whether it is used.** One sentence changed, and the skill went from never loading for the scenario prompts to loading in 3 of 6. A prompt that says exactly what to do, in a hurry, still skipped it.
- **An agent's instruction gave way under time pressure** in 2 of 3 runs. A team that needs a reviewer that can't edit needs the harness to enforce it, not an instruction alone. Denying the `edit` permission in the agent's file is the likely way, though it wasn't tried here, and an agent allowed to run shell commands could still write files through them.

These are 3 samples per scenario on one model, so treat the rates as indicative, not settled.
