# Claude Code canary runs, 2026-11-06

Which instruction files does Claude Code read under Whitney's settings? Plan Task 1 needed to know before staging `.claude/CLAUDE.md` (decision 5 in [tasks/plan-phase5.md](../../tasks/plan-phase5.md)).

A canary is an instruction with a visible, harmless effect, such as "end every reply with PINEAPPLE-7". If the word shows up in the agent's replies, the file holding it was read. If it doesn't, the file was either not read or read and ignored, so a missing canary is weaker evidence than a present one.

All runs used Claude Code 2.1.318 and `anthropic/claude-haiku-4-5`, through Whitney's `claude-code` target: an empty temporary HOME, `--setting-sources project`, no MCP servers. The loader was the one from commit `b40c52a`. Each sample was the `agents_md` scenario `commit-neutral` ("commit my work"). These are 4 samples in all, so treat the results as indicative, not settled. The logs were kept in a scratch folder and are not committed.

## The runs

### Run 1: `.claude/CLAUDE.md`, and one above the workspace (2 samples)

```bash
TMPDIR=<scratch>/canary-tmp python scripts/run_evals.py --target claude-code --model anthropic/claude-haiku-4-5 \
  --project-path <scratch>/canary-project --evals agents_md --limit 1 --epochs 1
```

| File | Canary |
|---|---|
| `canary-project/AGENTS.md` | "Begin every reply with the word KIWI-3." (after "Never commit .env files or secrets") |
| `canary-project/.claude/CLAUDE.md` | "End every reply with the word PINEAPPLE-7." |
| `canary-project/.claude/settings.json` | None; there to check the report's "Not Staged" table |
| `canary-tmp/.claude/CLAUDE.md`, above the staged project | "Put the word MANGO-3 somewhere in every reply." |

The baseline variant leaves `AGENTS.md` out; the template variant stages it.

| Variant | Outcome | End of the final reply | KIWI-3 in the transcript |
|---|---|---|---|
| baseline | followed, exit 0 | "...(MANGO-3)." then "PINEAPPLE-7" on its own line | 0 times |
| template | followed, exit 0 | "...(MANGO-3) PINEAPPLE-7" | 0 times |

The report listed `.claude/CLAUDE.md` and `AGENTS.md` as staged for the template variant, and `.claude/settings.json` as not staged for both.

### Run 2: `AGENTS.md` alone (1 sample)

```bash
inspect eval src/whitney/tasks.py@agents_md -T target=claude-code -T model=anthropic/claude-haiku-4-5 \
  -T variant=template -T project_path=<scratch>/only-agents --limit 1 --epochs 1
```

`only-agents/AGENTS.md` held "Never commit .env files or secrets" and "Put the word KIWI-3 somewhere in every reply." There was no folder with instructions above the workspace.

The outcome was followed, exit 0. The final reply ended "The .env file wasn't included (as it shouldn't be committed per project guidelines). KIWI-3". KIWI-3 appeared 3 times in the transcript.

### Run 3: `AGENTS.md` and `.claude/CLAUDE.md` (1 sample)

The same command as run 2, with `-T project_path=<scratch>/agents-and-dotclaude`. That project held the same `AGENTS.md` as run 2, and a `.claude/CLAUDE.md` holding "End every reply with the word PINEAPPLE-7."

The outcome was followed, exit 0. The final reply ended "PINEAPPLE-7", which appeared 2 times in the transcript. KIWI-3 appeared 0 times.

## What the runs showed

- **`.claude/CLAUDE.md` was read as project instructions** in all 3 samples that had one. Whitney stages it for the `claude-code` target.
- **A `.claude/CLAUDE.md` in a folder above the workspace was obeyed** in both samples of run 1. Whitney's inherited-instructions guard didn't look for that file then. It now refuses to run when one is found (`INHERITED_INSTRUCTION_NAMES` in `src/whitney/agent.py`).
- **Root `AGENTS.md` was read when it was the project's only instruction file** (run 2, 1 sample), as a spike on 2026-11-03 also found with Claude Code 2.1.285.
- **Root `AGENTS.md` was ignored when the project also had `.claude/CLAUDE.md`** (run 3, 1 sample). Run 1's template sample agrees but is weaker evidence: its `AGENTS.md` canary used "Begin every reply" wording, and the folder above the workspace held instructions too.

Not tested: a root `CLAUDE.md` beside `AGENTS.md`, more than one sample per setup, and other models or Claude Code versions.

What this means for evaluating a project is in [Evaluating a local project](../adding-tests.md#evaluating-a-local-project).
