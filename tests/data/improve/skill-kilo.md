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
- Evidence: neither validate's description warnings nor `description-says-when` speak about its description, so the description alone can't be relied on to load it
- Try: **not tested by Whitney.** On Kilo a hook is a plugin: add the file below to the project's `.kilo/plugin/`, and replace `<words from the trigger prompts>` with words from the prompts that should load the skill. Its `chat.message` hook notes that the prompt is one the skill covers, and its `experimental.chat.system.transform` hook adds the same line to the system prompt. Kilo marks that hook experimental, so it may change. Whitney's spike (Kilo 7.8.27, Haiku) didn't show the reminder raising how often a skill loads: the skill loaded in 3 of 3 runs with it, but also in the one run where the plugin didn't run. The stricter step is a `tool.execute.before` hook that refuses other tools until `skill` is called, which the spike did see refuse. A plugin is code that runs inside Kilo's own process, where the Kilo login can be read, and it can change what the model is sent. Whitney runs Kilo with `--pure`, which turns plugins off, so a Whitney run leaves the plugin out.
- How to tell: Add the hook, then run `asks`, `b` in Kilo itself, without `--pure`, since a Whitney run leaves the hook out: the skill should load in each.
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
