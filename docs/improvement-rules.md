# The improvement report's rules

`whitney run` and `whitney report` give each report a section, "How to improve the files under test", that says what to change in each file an eval file tests. It comes after the tables, and after the lists of files not run or left out by `--only`, and before "Evaluation Instructions and Assets". This page lists the rules behind it: what fires each one, what it advises, and where the rule comes from. Every rule is fixed: no model writes any of the report. Each one rests on published guidance from the harness or model provider, read on 2026-11-11, or for rule 3 also on a Whitney spike. Where a number is Whitney's own, not a source's, this page says so.

If building the section fails, the report says so in one line in its place, naming the error, and the report and the gate go on without it.

The starting list was the maintainer's `rules-audit` skill, which audits instructions for prompt engineering practice and progressive disclosure. Each of its rubric items that a fixed check can apply, and that published guidance backs, is a rule here. [Left out](#left-out) lists the rest and why.

## How the report decides what it can say

For each eval file whose logs name it, the section has two parts, kept apart.

**What the runs showed** reads the template's counts:

- **Not enough runs to say** when each scenario ran once in the template, or fewer than `--min-conclusive` of the template's runs were conclusive (50% unless the run set it). No run finding is given then.
- **The template failed in N of M conclusive runs**, then each failing scenario or trigger prompt, with its count, its `pressure`, and what the check said of its first failing run in the template, with the home folder of whoever ran the report written `~`.
- **Nothing to change** when the template held in every conclusive run and a baseline failed: the file is doing its job, and the text rules aren't applied to it.
- Otherwise, **the template held, but no baseline ran or none failed**, so the runs can't show the file made the difference.
- The one rule that reads the runs, [`hook-to-load-skill`](#hook-to-load-skill), comes after these lines.

**What the file's text suggests** applies the text rules to the file under test, as the template's log recorded it (`evaluation_assets`), so it is the text the run used. If the template didn't fail in enough runs, each finding is labelled **Suggestion**: the text shows it, the runs don't. The text rules read Markdown files only, so a config such as `kilo.jsonc` gets a line saying they have nothing to say about it.

Each finding gives its evidence (a quoted line of the file, or the runs' counts), what to try, how to tell whether it worked, and its source. For a text rule, "how to tell" is the next run of the same eval file with as many epochs: the template should hold at least as often, and the scenarios that failed should fail less often. Quoted text is put in a code span that nothing inside it can close, so a project's own text can't break the report's Markdown.

## Sources

| Key | Source | Read |
|---|---|---|
| Prompting | Anthropic, [Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices) | 2026-11-11 |
| Skills | Anthropic, [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) | 2026-11-11 |
| Memory | Claude Code, [How Claude remembers your project](https://code.claude.com/docs/en/memory) | 2026-11-11 |
| Kilo AGENTS.md | Kilo, [agents.md](https://kilo.ai/docs/customize/agents-md) | 2026-11-11 |
| Hooks | Claude Code, [hooks reference](https://code.claude.com/docs/en/hooks) | 2026-11-11 |
| Plugins | Kilo, [plugins](https://kilo.ai/docs/automate/extending/plugins) | 2026-11-11 |
| Hook spike | [spike-findings.md, "Phase 5 Task 40: hooks to enforce a skill"](spike-findings.md#phase-5-task-40-hooks-to-enforce-a-skill-2026-11-11) | spike |

## Rule 1: prompt engineering practice

These read list items: lines starting `-`, `*`, `+` or a number, with the indented lines that continue them, outside the frontmatter and fenced code. A sub-bullet indented under an item, with no blank line between, is an item of its own and is also read with its parent, so `- Never commit .env` followed by `  - Why: they hold secrets` gives a reason. Before matching words, each code span and each passage in double quotes counts as one word, so a word inside them, such as the `no` in `--no-verify`, directs nothing; the quoted line in the report is the file's own. "e.g.", "i.e.", "etc.", "vs." and "cf." don't end a sentence.

### `say-what-to-do`

- **Fires on** a list item of one sentence, with its sub-bullets, that starts with "Never", "Don't", "Do not", "Avoid" or "No", and has no "instead", "rather", "unless", "but", "only" or "except", and no second clause, after a comma or semicolon, starting with a verb that tells the agent what to do ("Don't use npm, use pnpm."). The verbs are a fixed list in `whitney/improve.py` (`DOES_INSTEAD`): add, ask, call, check, choose, commit, copy, create, keep, leave, let, make, mark, move, name, open, pass, pick, prefer, put, raise, read, return, run, say, send, set, stop, store, tell, try, update, use, wait and write, optionally after "and", "then", "always" or "just". A fixed list, so a list of names such as "Never use eval, exec or compile" isn't taken for one. An item of two or more sentences is taken to say what to do, since telling an alternative from a reason needs judgement.
- **Advises** adding what to do in its place.
- **Source:** Prompting, "Tell Claude what to do instead of what not to do".
- **Misses** a rule that forbids without the words above. Whitney's recorded run that reworded a rule to say what to do ([results/2026-11-10-kilo-prompt-capture.md](results/2026-11-10-kilo-prompt-capture.md), 0 of 6 to 6 of 6 on Claude Code) started from "Ask before modifying database schema", which this rule doesn't fire on, so the run isn't evidence for the rule and the report doesn't cite it.

### `give-a-reason`

- **Fires on** a bullet that directs and gives no reason after the word that directs. It directs when its own text, not a sub-bullet's, has "never", "always", "must", "should", "shall", "don't", "do not" or "avoid", or starts with "No" ("No force pushes."; a later "no", as in "Python 3.11, no external services", describes). It gives a reason when, after that word, it or a sub-bullet has "because", "since", "so", "otherwise", "which", "why", "in order to", a colon, a semicolon, a dash or a bracket; a colon or dash straight after the word, as in "**Never:** commit secrets", is a label, not a reason, and a reason before the word isn't counted. Numbered items are steps, which need no reason; a bullet without those words, such as "**The file under test**, such as `AGENTS.md`", describes rather than directs; and an item of two sentences, with its sub-bullets, is taken to give one. A sentence may end inside emphasis, as in "**Never edit the lockfile.** Run the package manager instead."
- **Advises** saying why the rule matters.
- **Source:** Prompting, "Add context to improve performance": "Providing context or motivation behind your instructions, such as explaining to Claude why such behavior is important, can give Claude more information about your goals", with "NEVER use ellipses" as the less effective form.

### `no-shouting`

- **Fires on** three or more of MUST, NEVER, ALWAYS, CRITICAL, IMPORTANT and REQUIRED in capitals, outside code.
- **Advises** ordinary case, with capitals kept for one or two hard limits.
- **Source:** Prompting, "Tool usage": current models "might now overtrigger. The fix is to soften any aggressive language. Where you might have said 'CRITICAL: You MUST use this tool when...,' you can use more normal prompting". The number three is Whitney's: no source gives one. Skills, "Develop Skills iteratively", mentions "stronger language such as 'MUST filter'" as one fix a reviewer might suggest, so the rule fires on overuse, not on one word.

### `description-says-when`

- **Fires on** a skill whose `description` has none of "when", "whenever", "before", "after", "if" or "while". A skill with no description gets `whitney validate`'s warning instead.
- **Advises** saying when to use the skill, naming the requests and files it covers.
- **Source:** Skills, "Writing effective descriptions": the description "should include both what the Skill does and when to use it".
- **Misses** a description that says when in words other than those above, and one that says when but too vaguely. In Whitney's recorded run ([results/2026-11-10-skills-agents.md](results/2026-11-10-skills-agents.md)) the `release-notes` skill never loaded for the scenario prompts until its description named CHANGELOG.md, and then loaded in 3 of 6; but the old description already said "when the user asks…", so this rule wouldn't have fired on it, and the report doesn't cite the run.
- **Followed through once,** 2026-11-12, on `claude-code` with three epochs ([results/2026-11-12-improvement-report-runs.md](results/2026-11-12-improvement-report-runs.md#runs-b-and-c-one-finding-followed-through)): the rule fired on a description that said what but not when; with the description changed as advised, the template's scenarios went from 2/6 to 5/6, and the skill loaded in them in 3 of 6 instead of 0. Every count moved the right way, but within noise at three epochs, so the run neither shows the advice helps nor that it doesn't, and the report doesn't cite it.

### `whitney-validate`

- **Fires on** `whitney validate`'s warnings about the skill's description (none, or nearly the same as another skill's), [Warnings about skill descriptions](adding-tests.md#warnings-about-skill-descriptions). The report repeats them in validate's words; it adds no check of its own here. Validate names a near-duplicate pair at the later skill's path only, so the report also gives that warning to the skill it names as the other one, saying which file validate put it on.
- **Rough edge:** validate's words are Kilo's ("Kilo picks a skill by its description", "Kilo can't tell which to load"), and a `claude-code` report quotes them unchanged.
- **Source:** Skills, "Writing effective descriptions".

## Rule 2: progressive disclosure

### `always-loaded-size`

- **Fires on** an instruction file over 200 lines that is loaded in every session: not a skill, an agent or a command, and not a rules file scoped by `paths` in its frontmatter.
- **Advises** keeping what every task needs, and moving instructions for one kind of task, or one part of the codebase, into a skill, or on Claude Code into a rules file with `paths`.
- **Source:** Memory, "Write effective instructions": "target under 200 lines per CLAUDE.md file. Longer files consume more context and reduce adherence", and "If an entry is a multi-step procedure or only matters for one part of the codebase, move it to a skill or a path-scoped rule instead." Kilo AGENTS.md says "Keep it concise" and gives no number, so the report uses Claude Code's on both targets.

### `skill-body-size`

- **Fires on** a `SKILL.md` whose body, after the frontmatter, is over 500 lines. When the skill has no other file, the advice says so: it holds everything and has no `references/`.
- **Advises** moving detail into files beside it, each linked from `SKILL.md` with when to read it.
- **Source:** Skills, "Progressive disclosure patterns": "Keep the SKILL.md body under 500 lines for optimal performance. Split content into separate files when approaching this limit."

### `one-level-deep`

- **Fires on** a Markdown file in the skill's folder, other than `SKILL.md`, with a Markdown link to another of the skill's files.
- **Advises** linking each file from `SKILL.md` directly.
- **Source:** Skills, "Avoid deeply nested references": "Keep references one level deep from SKILL.md", because "Claude might partially read files when they're referenced from other referenced files."

## Rule 3: a hook to enforce a skill

### `hook-to-load-skill`

- **Fires on** the runs, never on the text alone. All of these must hold: the eval file tests a skill; the runs are enough to say (above); neither validate's description warnings nor `description-says-when` speak about the skill's description; at least 4 of the template's runs could tell whether the skill loaded; and it didn't load in at least 25% of them. The runs that could tell are the template's scenario runs whose score says whether the skill loaded, and its positive trigger prompts, which pass when it loads, each counted once; negative trigger prompts, where the skill shouldn't load, and known gaps are left out. The 4 and the 25% are Whitney's own numbers, not a source's: under 4 runs one miss is too much chance, and under a quarter missed the skill loads often enough that a hook, which runs as the user, may not be worth adding. When the description fails a check, rule 1 speaks instead.
- **Advises**, labelled **not tested by Whitney**, the hook for the run's target, with the skill's name filled in:
  - `claude-code`: a `UserPromptSubmit` command hook in the project's `.claude/settings.json` that runs `.claude/hooks/<skill>-reminder.sh`, which prints one line naming the skill when the prompt is one it covers. The stricter step is named, not written out: a `PreToolUse` hook that refuses other tools until `Skill` is called.
  - `kilo`: a plugin, `.kilo/plugin/<skill>-reminder.ts`, whose `chat.message` hook notes the prompt is one the skill covers and whose `experimental.chat.system.transform` hook adds the same line to the system prompt. The report says Kilo marks that hook experimental, that the spike didn't show the reminder raising how often a skill loads, and that the stricter step is a `tool.execute.before` hook that refuses other tools until `skill` is called.
  - Both say what a hook is: on Claude Code a shell command run as the user, with the API key in its environment, which a `claude -p` run starts without asking; on Kilo code inside Kilo's own process. A skill name that couldn't go into the script as it is, such as one with `$` in it, is left as `<skill>` for the reader to fill.
- **How to tell:** add the hook, then run the skill's positive trigger prompts, and the scenarios where it didn't load, in the harness itself. A Whitney run leaves the hook out: Whitney stages no hooks, and runs Kilo with `--pure`, which turns plugins off.
- **Source:** Hooks (`UserPromptSubmit` stdout is added to the model's context; `PreToolUse` can refuse a call); Plugins; and the hook spike. On Claude Code (2.1.322, Haiku, one skill and one prompt) the skill loaded in 1 of 6 runs with no hook and 7 of 7 with a reminder. On Kilo (7.8.27, Haiku) the reminder's effect wasn't shown: the skill loaded in 3 of 3 runs with it, but also in the one run where the plugin didn't run.
- **Seen firing in a recorded run,** 2026-11-12, on `claude-code` with three epochs: the example's `release-notes` skill loaded in 9 of 12 runs that could tell, exactly the 25% missed the rule needs ([results/2026-11-12-improvement-report-runs.md](results/2026-11-12-improvement-report-runs.md#rule-3-fired-on-run-c)). The advice wasn't tried.

## Left out

These `rules-audit` rubric items are not rules here.

| Rubric item | Why it is left out |
|---|---|
| Critical rules first | Needs judgement: which rule is critical. |
| Stated once | Needs judgement: telling a rule restated in other words from two rules. |
| Header hierarchy | A fixed check could apply it (one H1, nothing deeper than H3), but no published source found gives that rule. Memory and Kilo AGENTS.md say only to use headers and bullets. |
| List semantics | Needs judgement: whether a list is ordered steps or independent rules. |
| Depth matches frequency | Needs judgement: how often a task needs a piece of detail. `always-loaded-size` covers the part a number can. |
| Entry file is a map | Needs judgement. |
| Triggered pointers | A fixed check could look for "when" beside a link, but the published guidance only shows the pattern ("For tracked changes: See REDLINING.md") without stating it as a rule. Left for a later version. |
| Self-contained references | Needs judgement. |
| No redundant rules | Needs judgement: what the model would do anyway. A baseline that passes as often as the template is the run's version of this. |
| No contradictions | Needs judgement. Memory says contradictions make Claude "pick one arbitrarily", and Claude Code's `/doctor prompt-audit` looks for them with a model. |
| Examples fenced and varied | Needs judgement: what counts as an example. |
| Data separated | Needs judgement. |
| Format bleed | Needs judgement: whether the output's format matters. |
| Rationalisation table, Real excuses | Need judgement. |
| Checkable | Needs judgement. |
| Rule IDs | A fixed check could apply it, but no published source found asks for rule IDs. |

## What the rules can't tell

- The text rules match words, not meaning. "Never push to main; propose a pull request" says what to do, yet `say-what-to-do` fires on it, since "propose" isn't on its list of verbs; and its semicolon is enough for `give-a-reason` to take it as giving a reason, which it doesn't. Read each finding before acting on it.
- A rule that fires on the file's text isn't shown to matter until a run fails without the change and passes with it. Each finding says which run to compare.
- Rule 3's advice has never been tested in a Whitney run. To test a hook, Whitney would need an opt-in that names what runs, Docker, `--include-hook-events`, a way to tell a hook's refusal from a permission rule's, and on Kilo a run without `--pure` (spike-findings.md).
