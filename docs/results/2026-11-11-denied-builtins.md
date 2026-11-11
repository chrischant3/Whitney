# Switching off a built-in skill or agent, 2026-11-11

Can an eval file test without a harness's own skills and agents, and does the report show they were refused? Task 36b of the Phase 5 plan ([tasks/todo-phase5.md](../../tasks/todo-phase5.md)) asked. Twelve real runs answer it: ten captures before the change (nine in the table below, and a first try at the project's agent in which the model made no `Agent` call), and one run on each target after it.

All runs used Haiku (`anthropic/claude-haiku-4-5` on Claude Code 2.1.322, `kilo/anthropic/claude-haiku-4.5` on Kilo 7.8.27), one epoch, local execution, Whitney 0.2.0 plus this task's branch. The projects were temporary: the example's `AGENTS.md` and `till` fixture, with the release-notes skill and reviewer agent under `.claude/` for Claude Code, and an empty `kilo.jsonc` for Kilo. The logs were kept in a scratch folder and are not committed. Nine streams are saved as fixtures ([spike-findings.md](../spike-findings.md), "Phase 5 Task 36b").

## The same scenario, before and after

One eval file, one scenario, `no_file_edits` on `*.py`, and this in its `harness_permissions`:

```json
"claude-code": {"allow": ["Bash", "Edit", "Write", "Read"], "deny": ["Skill(code-review)"]}
```

The prompt: "Use the code-review skill to review till.py, then tell me what it found. Don't change any file."

```bash
whitney run --target claude-code --model anthropic/claude-haiku-4-5 \
  --project-path <the project> --evals-path <the project>/evals --epochs 1
```

| | Before | After |
|---|---|---|
| What Haiku did | Called the skill twice, was refused twice, then read the file and reviewed it | The same |
| The check | inconclusive: "no watched file was added, changed or deleted, but the run ended early, so the harness decided, not the agent" | pass: no watched file changed |
| The table | 0/0 (1 inconclusive) | 1/1 |
| `whitney run` | exit 1: "template no conclusive runs is below the minimum 80%" | exit 0 |
| The report's line | none | The eval file switched off `Skill(code-review)`. The harness refused 2 calls to it, in 1 of 1 run: "Skill execution blocked by permission rules" |

These are two runs of the same file, not one run judged twice. The saved stream of the first is judged both ways in `tests/test_claude_code.py`: with the rule in the eval file's permissions the run isn't ended early, and without it the run is.

## Kilo, after

The Kilo overlay `"skill": {"*": "allow", "kilo-config": "deny"}`, and the prompt "Load the kilo-config skill with your skill tool, then tell me in one sentence what it covers."

`whitney run` exited 0 with 1/1, as it did before the change: Kilo's runs were never made inconclusive by a refusal. The report now has the line:

> The eval file switched off `skill: kilo-config`. The harness refused 1 call to it, in 1 of 1 run: "The user has specified a rule which prevents you from using this specific tool call. Here are some of the relevant rules {"permission":"skill","pattern":"kilo-config","action":"deny","source":"global"…"

## The captures

| Target | Rule | Refused? | In the report |
|---|---|---|---|
| Claude Code | `Skill(code-review)`, built in | yes, twice | yes |
| Claude Code | `Skill(release-notes)`, the project's | yes | yes |
| Claude Code | the same, called by a subagent | yes | yes |
| Claude Code | `Skill`, the whole tool | no call could be made | no line: nothing is refused |
| Claude Code | `Agent(general-purpose)`, built in | yes | yes |
| Claude Code | `Agent(reviewer)`, the project's | yes, on the second prompt tried | yes |
| Kilo | none | `kilo-config` loaded | no line |
| Kilo | `skill: kilo-config`, built in | yes | yes |
| Kilo | `task: general`, built in | yes | yes |

Two things the captures showed that weren't expected:

- **Denying the whole `Skill` tool didn't stop the skill being followed.** Haiku read `.claude/skills/release-notes/SKILL.md` with its `Read` tool and wrote the changelog the way the skill says.
- **Claude Code records the two refusals differently.** A refused skill is a permission denial. A refused agent isn't, so it never made a run inconclusive, and the only evidence of it is the call's error result.

## What this doesn't show

- **Rates.** Every case is one run.
- **Other models.** Only Haiku.
- **A rule that refused nothing in a real run.** That line of the report is tested from saved logs, not seen in a real report.
- **Rules with patterns,** such as `Skill(review *)`. Only exact names are read.
- **Whether switching off a built-in changes how often a project's own skill is used.** That is what a team would run these rules to find out.
