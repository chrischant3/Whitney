## How to improve the files under test

Fixed rules, listed with their sources in `docs/improvement-rules.md`; no model wrote this section. What the runs showed is kept apart from what the file's text suggests.

### Team rule

File under test: `AGENTS.md` · eval file `team.json`

**What the runs showed**

The template held in all 2 conclusive runs, but no baseline failed (`no_rules` 2/2, `other` 2/2), so the runs can't show the file made the difference.

**What the file's text suggests**

No failing run is behind these, so each is a suggestion: the file's text shows it, the runs don't.

**Suggestion: `say-what-to-do`** in `AGENTS.md`: a rule says what not to do, and not what to do instead.

- Evidence: line 1: `Never push to main`
- Try: add what to do in its place, as well as what not to do. In a recorded run, rewording a rule that way took its scenarios from 0 of 6 to 6 of 6 on Claude Code.
- How to tell: Run `team.json` again with as many epochs: the template, now 2/2, should hold at least as often.
- Source: [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), read 2026-11-11; the recorded run `docs/results/2026-11-10-kilo-prompt-capture.md`.

**Suggestion: `give-a-reason`** in `AGENTS.md`: a rule gives no reason.

- Evidence: line 1: `Never push to main`
- Try: say why the rule matters, so the model can apply it to cases the rule doesn't name.
- How to tell: Run `team.json` again with as many epochs: the template, now 2/2, should hold at least as often.
- Source: [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), read 2026-11-11.
