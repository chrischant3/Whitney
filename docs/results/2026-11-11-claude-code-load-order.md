# Claude Code load order: two confirming runs (2026-11-11)

Task 34 changed the `claude-code` loader to copy what Claude Code's [memory docs](https://code.claude.com/docs/en/memory.md) say it reads. These two runs check the two cases the earlier spike didn't cover.

## Setup

- Claude Code 2.1.322, `claude-haiku-4-5`, one run per case.
- Whitney's own flags: `--setting-sources project`, `--strict-mcp-config`, `--settings '{"permissions":{}}'`, `--output-format stream-json --verbose`.
- An empty temporary HOME and no git config.
- The prompt was "Say hello in one sentence." Each instruction file told the agent to end every reply with its own word.

## Results

| Project holds | Reply ended with | What it shows |
|---|---|---|
| `CLAUDE.md` and `.claude/CLAUDE.md` | both words, root first | Claude Code loads both together, so Whitney copies both |
| `.claude/AGENTS.md` only | its word | Claude Code reads `.claude/AGENTS.md` with no `CLAUDE.md`, so Whitney copies it |

Both runs exited 0. The streams were kept in a scratch folder and aren't committed, because they hold local paths.

The cases with a `CLAUDE.md` beside an `AGENTS.md` were run in the spike of the same day (`docs/spike-findings.md`, "Which instruction file Claude Code reads"): Claude Code followed the `CLAUDE.md`.
