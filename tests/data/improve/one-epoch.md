## How to improve the files under test

Fixed rules, listed with their sources in `docs/improvement-rules.md`; no model wrote this section. What the runs showed is kept apart from what the file's text suggests.

### Team rule

File under test: `AGENTS.md` · eval file `team.json`

**What the runs showed**

Not enough runs to say: each scenario ran once in the template, and one run can pass or fail by chance. Run it again with `--epochs 3` or more.

**What the file's text suggests**

No failing run is behind these, so each is a suggestion: the file's text shows it, the runs don't.

**Suggestion: `say-what-to-do`** in `AGENTS.md`: a rule says what not to do, and not what to do instead.

- Evidence: line 4: `NEVER skip it.`
- Evidence: line 6: `Never push to main`
- Evidence: line 7: `Don't merge on Friday`
- Evidence: and 2 more like them
- Try: add what to do in its place, as well as what not to do.
- How to tell: Run `team.json` again with as many epochs: the template, now 1/2, should hold at least as often, and `b` should fail less often.
- Source: [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), read 2026-11-11.

**Suggestion: `give-a-reason`** in `AGENTS.md`: a rule gives no reason.

- Evidence: line 3: `You MUST test.`
- Evidence: line 4: `NEVER skip it.`
- Evidence: line 5: `ALWAYS lint.`
- Evidence: and 4 more like them
- Try: say why the rule matters, so the model can apply it to cases the rule doesn't name.
- How to tell: Run `team.json` again with as many epochs: the template, now 1/2, should hold at least as often, and `b` should fail less often.
- Source: [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), read 2026-11-11.

**Suggestion: `no-shouting`** in `AGENTS.md`: capitals such as MUST and NEVER are used often.

- Evidence: 3 such words
- Evidence: line 3: `- You MUST test.`
- Evidence: line 4: `- NEVER skip it.`
- Evidence: line 5: `- ALWAYS lint.`
- Try: write them in ordinary case, and keep capitals for the one or two hard limits: current models follow ordinary wording, and emphatic wording makes them overtrigger.
- How to tell: Run `team.json` again with as many epochs: the template, now 1/2, should hold at least as often, and `b` should fail less often.
- Source: [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices), read 2026-11-11.

**Suggestion: `always-loaded-size`** in `AGENTS.md`: an instruction file loaded in every session is over 200 lines.

- Evidence: 210 lines, loaded in every session
- Try: keep what every task needs, and move instructions for one kind of task, or one part of the codebase, into a skill, which loads only when it is needed, or on Claude Code into a rules file with `paths`.
- How to tell: Run `team.json` again with as many epochs: the template, now 1/2, should hold at least as often, and `b` should fail less often.
- Source: [How Claude remembers your project](https://code.claude.com/docs/en/memory), read 2026-11-11.
