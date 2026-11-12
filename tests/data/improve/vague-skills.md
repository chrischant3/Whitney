## How to improve the files under test

Fixed rules, listed with their sources in `docs/improvement-rules.md`; no model wrote this section. What the runs showed is kept apart from what the file's text suggests.

### Team rule

File under test: `.claude/skills/release-notes/SKILL.md` · eval file `team.json`

**What the runs showed**

The template held in all 2 conclusive runs, but no baseline ran, so the runs can't show the file made the difference.

**What the file's text suggests**

No failing run is behind these, so each is a suggestion: the file's text shows it, the runs don't.

**Suggestion: `whitney-validate`** in `.claude/skills/release-notes/SKILL.md`: `whitney validate` warns about the skill's description.

- Evidence: `whitney validate`: `no description in its frontmatter; Kilo picks a skill by its description, so the agent can't tell when to load this one`
- Try: fix what the warning names.
- How to tell: Run `team.json` again with as many epochs: the template, now 2/2, should hold at least as often.
- Source: [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), read 2026-11-11.

### Debug skill

File under test: `.kilo/skills/debug/SKILL.md` · eval file `skills/debug.json`

**What the runs showed**

The template held in all 2 conclusive runs, but no baseline ran, so the runs can't show the file made the difference.

**What the file's text suggests**

No failing run is behind these, so each is a suggestion: the file's text shows it, the runs don't.

**Suggestion: `description-says-when`** in `.kilo/skills/debug/SKILL.md`: the skill's description doesn't say when to use it.

- Evidence: description: `Debugging help.`
- Try: add when to use the skill, naming the requests and files it covers, such as "Use when the user asks for …". The model picks a skill by its description.
- How to tell: Run `skills/debug.json` again with as many epochs: the template, now 2/2, should hold at least as often.
- Source: [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), read 2026-11-11.
