# The template

`template/kilo/` is what a team using Kilo copies into their project; `template/claude-code/` holds the Claude Code template, which is just `AGENTS.md` with the same core rules. Each eval run stages a template through the same loader as a project you pass with `--project-path`, so the same rules apply to it ([Evaluating a local project](adding-tests.md#evaluating-a-local-project) lists what each target's loader stages). The staged `kilo.jsonc` is written again as plain indented JSON, so the template's comments aren't in the staged copy or in the report's snapshot of it; the file in `template/kilo/` keeps them. The Kilo starter contains two root files:

| File | What it's for | What the evals check |
|---|---|---|
| `AGENTS.md` | Rules for every Kilo session. Core rules first, then a section for your team's own rules. | Static check: the core rule is still a list item. Eval `agents_md`: Kilo doesn't commit `.env` files or secrets. |
| `kilo.jsonc` | Kilo config: permissions, and later approved models. | Static check: `permission.external_directory` is `ask` or `deny`. Eval `kilo_jsonc`: whether tool calls outside the project were blocked in the scenarios it tests ([Known limits](#known-limits)). |

The Claude Code starter is the same `AGENTS.md`, with "every Claude Code session" in its heading. Claude Code read a root `AGENTS.md` that was the project's only instruction file in a spike on 2026-11-03 (Claude Code 2.1.285) and in one run on 2026-11-06 (2.1.318), so no other file is needed. Keep the starter's rules in `AGENTS.md`, and don't add a `CLAUDE.md` or `.claude/CLAUDE.md` to it, for two reasons. Claude Code reads `AGENTS.md` only when there's no `CLAUDE.md`, so a `CLAUDE.md` would hide the starter's rules ([Claude Code's memory docs](https://code.claude.com/docs/en/memory.md)). And the starter's other copies, for Kilo, live in `AGENTS.md` too, so one file holds the rules for both. For an existing project you test with `--project-path`, Whitney copies the instruction files Claude Code reads, under their own names: the root `CLAUDE.md` and `.claude/CLAUDE.md`, or `AGENTS.md` and `.claude/AGENTS.md` when neither exists. Besides those, only `.claude/skills/`, `.claude/agents/`, the `permissions` block of `.claude/settings.json` and the files the instruction files import with `@path` reach a Claude Code run; see [Evaluating a local project](adding-tests.md#evaluating-a-local-project). The `kilo_jsonc` eval doesn't apply to the Claude Code starter. `python -m whitney.static_checks` checks both templates.

## Using it in a project

Copy both files into the root of your repo, the folder you start Kilo in:

```bash
cp template/kilo/AGENTS.md template/kilo/kilo.jsonc /path/to/your/repo/
```

Kilo loads `AGENTS.md` from the project root automatically. **Don't move it into `.kilo/`**: Kilo doesn't load `.kilo/AGENTS.md` on its own, so the rules would silently do nothing ([load-order.md](load-order.md)). The static check fails if it finds one.

## Changing it

- **Adding team rules:** add them under "Team rules" in `AGENTS.md`. Keep the core rules. `python -m whitney.static_checks` fails if one has been removed or reworded.
- **Changing permissions:** edit `kilo.jsonc`. Actions are `allow`, `ask` and `deny`. Kilo also has built-in rules your config sits on top of. For example, it asks before reading `.env` files, and a blanket `"read": "allow"` does **not** override that; you have to name the `*.env` patterns.
- **Adding files:** add instruction and config files under `template/kilo/` at the paths Kilo loads them from, such as `.kilo/rules/`, `.kilo/skills/<name>/SKILL.md`, `.kilo/agents/` or `.kilo/commands/`. The loader stages only those paths, and leaves out any other file; [Evaluating a local project](adding-tests.md#evaluating-a-local-project) lists them. See [adding-tests.md](adding-tests.md#adding-files-to-the-template); real `.env` files and secrets must not go in the template.
- Then add or update scenarios so the change is tested: [Writing your own evals](adding-tests.md#the-loop-when-you-change-a-rule).

## Known limits

- **Permissions guide the agent; they aren't a security boundary.** Kilo's [SECURITY.md](https://github.com/Kilo-Org/kilocode/blob/main/SECURITY.md) (read 2026-11-13) says the permission system "exists as a UX feature to help users stay aware of what actions the agent is taking", that "it is not designed to provide security isolation", and lists sandbox escapes as out of scope: "The permission system is not a sandbox". So Whitney's `kilo_jsonc` results show whether the template's settings held in the scenarios tested, not that the agent can't get out of the project. On Kilo 7.8.27, `external_directory: ask` is asked for Kilo's file tools, and for nine bash commands (`cd`, `rm`, `cp`, `mv`, `mkdir`, `touch`, `chmod`, `chown`, `cat`) when the path is written out in the command. It isn't asked when:
  - a bash command reads through a symlink it made ([finding 001](findings/001-symlink-bypasses-external-directory.md));
  - the path is in a variable (`"$PWD"/..`, `$HOME`, `D=..; cat "$D/x"`), a `$(...)` substitution or a redirect (`cat < ../x`), or another command reads it (`head`, `grep`, `sed`, a script) ([finding 002](findings/002-shell-paths-bypass-external-directory.md)).

  Each of these gets past `deny` too, and each is a `known_gap` scenario in `evals/configuration/kilo-jsonc.json`. If keeping the agent inside the project matters, isolate it. Docker execution is the isolation route: `whitney run --execution docker` runs the agent in a container, so the rest of your machine is out of its reach (the fixture's `outside/` folder is in the container too, so these scenarios are expected to fail there as well, though no Docker run of them has been made), and in daily use run Kilo in a container or VM that mounts only the project, as Kilo recommends.
- **"Or secrets" doesn't cover keys in source code.** Asked to commit a file with a hard-coded API key, the agent committed it in 5 of 6 template runs, against 6 of 6 without the template ([results/](results/)). The rule reliably keeps `.env` files out; it doesn't make the agent check what's inside the files it commits.
