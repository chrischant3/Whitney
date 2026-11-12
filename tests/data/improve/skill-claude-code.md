## How to improve the files under test

Fixed rules, listed with their sources in `docs/improvement-rules.md`; no model wrote this section. What the runs showed is kept apart from what the file's text suggests.

### Team rule

File under test: `.claude/skills/release-notes/SKILL.md` · eval file `team.json`

**What the runs showed**

The template failed in 3 of 6 conclusive runs:

- `asks`: 1/2
- `b`: 0/2

**`hook-to-load-skill`** in `.claude/skills/release-notes/SKILL.md`: the skill's description is sound, and the skill still didn't load.

- Evidence: the skill `release-notes` loaded in 3 of 6 runs that could tell (the template's scenario runs and its positive trigger prompts)
- Evidence: its description passes `whitney validate`'s checks and `description-says-when`, so the description alone can't be relied on to load it
- Try: **not tested by Whitney.** Add a `UserPromptSubmit` command hook to the project's `.claude/settings.json`, below, which prints one line naming the skill when the prompt is one the skill covers; replace `<words from the trigger prompts>` with words from the prompts that should load it. The stricter step, if that isn't enough, is a `PreToolUse` hook that refuses other tools until `Skill` is called. A hook is a shell command Claude Code runs as the user, with the API key in its environment, and a `claude -p` run starts it without asking. Whitney stages no hooks, so a Whitney run leaves the hook out. In Whitney's spike (Claude Code 2.1.322, Haiku, one skill and one prompt) the skill loaded in 1 of 6 runs with no hook, and in 7 of 7 with a `UserPromptSubmit` or `SessionStart` reminder.
- How to tell: Add the hook, then run `asks`, `b` in Claude Code itself, since a Whitney run leaves the hook out: the skill should load in each.
- Source: [Claude Code hooks reference](https://code.claude.com/docs/en/hooks), read 2026-11-11; Whitney's spike, `docs/spike-findings.md`, "Phase 5 Task 40: hooks to enforce a skill".

`.claude/settings.json`:

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "sh \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/release-notes-reminder.sh"}]}]}}
```

`.claude/hooks/release-notes-reminder.sh`:

```sh
#!/bin/sh
if grep -qiE '<words from the trigger prompts>'; then
  echo "This project has a release-notes skill, and this request is one it covers. Load it with the Skill tool (skill: release-notes) before you answer, and follow it."
fi
```

**What the file's text suggests**

These come from the file's text, not from the runs: places to start on the failures above, not their proven cause.

**`skill-body-size`** in `.claude/skills/release-notes/SKILL.md`: the SKILL.md body is over 500 lines.

- Evidence: the body is 504 lines
- Try: move more of the detail into files beside it, such as `references/<topic>.md`, each linked from SKILL.md with when to read it.
- How to tell: Run `team.json` again with as many epochs: the template, now 3/6, should hold at least as often, and `asks`, `b` should fail less often.
- Source: [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), read 2026-11-11.

**`one-level-deep`** in `.claude/skills/release-notes/SKILL.md`: a skill's file other than SKILL.md links to another of its files.

- Evidence: `.claude/skills/release-notes/references/a.md` line 1: `Then read [b](b.md).`
- Try: link each file from SKILL.md directly, with when to read it: a model may read only part of a file it reaches through another linked file.
- How to tell: Run `team.json` again with as many epochs: the template, now 3/6, should hold at least as often, and `asks`, `b` should fail less often.
- Source: [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), read 2026-11-11.
