# A skill and an agent on Claude Code, one real run, 2026-11-11

Do the skill and subagent checks work on `claude-code` in a real run, and can their controls fail? Task 36 of the Phase 5 plan ([tasks/todo-phase5.md](../../tasks/todo-phase5.md)) asked for one real trigger run. This is it. It is one epoch, so it shows the checks working, not how often Haiku uses a skill.

## What was run

A temporary project, not committed, made from [`examples/consumer/`](../../examples/consumer/):

- `AGENTS.md`, unchanged.
- `.claude/skills/release-notes/` and `.claude/skills/debugging/`, copied from `.kilo/skills/`.
- `.claude/agents/reviewer.md`: the Kilo agent's file with `name: reviewer` added to its frontmatter and `mode: all` removed.
- `evals/skills/release-notes.json`, with `source` changed to `.claude/skills/release-notes/SKILL.md`.
- `evals/agents/reviewer.json`, with `source` changed to `.claude/agents/reviewer.md`. Its two scenarios used `"agent": "reviewer"`, which is Kilo's alone, so they were replaced by one that asks for the subagent by name: "Use the reviewer subagent to review refund() in till.py, then tell me what it found."
- The `till` fixture.

`whitney validate --target claude-code` found no problems. Then:

```bash
whitney run --target claude-code --model anthropic/claude-haiku-4-5 \
  --project-path <the project> --evals-path <the project>/evals --epochs 1
```

Claude Code 2.1.322, Whitney 0.2.0, local execution, 17 runs. The logs were kept in a scratch folder and are not committed. `whitney run` exited 1: the skill's file failed its gate, as below.

## The skill's trigger tests

| Prompt | Kind | template | trigger_control |
|---|---|---|---|
| asks-for-release-notes | positive | pass: "the skill release-notes was loaded" | fail: "the skill release-notes wasn't loaded; nothing was loaded" |
| asks-what-changed | positive | pass | fail, the same |
| asks-for-a-bug-fix | negative, owner `debugging` | **fail** | not run |
| asks-what-a-function-does | negative | pass | not run |

- Both positive prompts loaded the skill, and both failed in the control, which removes the skill's folder. So `skill_loaded` has now failed in a real run on `claude-code`.
- `asks-for-a-bug-fix` failed on its owner, not on the skill under test. `release-notes` stayed unloaded, as it should. But the prompt should load `debugging`, and Haiku fixed the bug without loading any skill.
- `skill_not_loaded` passed twice and has not yet failed in a real run on `claude-code`.

## The skill's scenarios

| Scenario | baseline | template |
|---|---|---|
| add-changelog-entry | 0/1 | 1/1 |
| paste-the-list-time-pressure | 0/1 | 0/1 |

The report's line: "In the template's 2 scenario runs, the skill `release-notes` was loaded in 1, wasn't in 1 and couldn't be told in 0." The run that didn't load the skill is the one that put a ticket id in `CHANGELOG.md`.

## The agent's trigger tests

| Prompt | Kind | template | trigger_control |
|---|---|---|---|
| asks-for-a-review | positive | pass: "the subagent reviewer was delegated" | inconclusive |
| asks-for-a-second-opinion | positive | pass | fail: "the subagent reviewer wasn't delegated; delegated: general-purpose" |
| asks-what-a-function-does | negative | pass | not run |

- Both positive prompts handed work to `reviewer`. In the control, without the agent's file, one handed the work to Claude Code's built-in `general-purpose` agent, and `subagent_delegated` failed. So it has now failed in a real run on `claude-code`.
- The agent's one scenario passed in both variants, 1/1 and 1/1. Asked for a review, nothing edited a `.py` file with or without the reviewer, so this scenario doesn't show the agent's rule doing anything.

## What the inconclusive run found

In the control's `asks-for-a-review`, Haiku had no reviewer to use and called Claude Code's built-in `code-review` skill. That skill ran as a subagent of its own for over 90 seconds, and Claude Code printed a `tool_progress` line every 30 seconds. Whitney didn't know that event type, so the check said:

> no call that delegated the subagent reviewer was seen, but 3 line(s) of the stream couldn't be read as events

That was the right answer for a stream it couldn't read in full. Two things changed afterwards:

- `tool_progress` is now an event the checks know. The run's stream is saved, sanitised, as `tests/data/claude-code-events/skill-forked.jsonl`, and a test judges it: `subagent_delegated` for `reviewer` now fails on it, as a control should.
- The stream showed a hand-off the delegation checks don't count. The skill's subagent was started by the `Skill` call, with no `Agent` call. [adding-tests.md](../adding-tests.md) says so.

## What this run doesn't show

- **Rates.** One epoch each. The gate failed on the skill's file (scenarios 50%, trigger tests 75%, both under 80%), and with one run per prompt that says little. Since Task 29, later the same day, the gate reads positive and negative trigger tests separately: `whitney report` on this run's logs still exits 1, and its second line is now "template negative trigger tests 50% is below the minimum 80%" (positive 2/2, negative 1/2). See [2026-11-11-trigger-gates.md](2026-11-11-trigger-gates.md).
- **`skill_not_loaded` and `subagent_not_delegated` failing.** Neither failed. They are tested against saved Claude Code streams that make them fail.
- **A subagent loading a skill.** No run here did. The saved stream `subagent-loads-skill` covers it.
