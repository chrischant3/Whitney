# The template

`template/kilo/` is what a team using Kilo copies into their project; `template/claude-code/` holds the Claude Code template, which is just `AGENTS.md` with the same core rules. Files are staged recursively, preserving relative paths. The Kilo starter contains two root files:

| File | What it's for | What the evals check |
|---|---|---|
| `AGENTS.md` | Rules for every Kilo session. Core rules first, then a section for your team's own rules. | Static check: the core rule is still a list item. Eval `agents_md`: Kilo doesn't commit `.env` files or secrets. |
| `kilo.jsonc` | Kilo config: permissions, and later approved models. | Static check: `permission.external_directory` is `ask` or `deny`. Eval `kilo_jsonc`: tool calls outside the project are blocked. |

The Claude Code starter is the same `AGENTS.md`, with "every Claude Code session" in its heading. Claude Code read a root `AGENTS.md` that was the project's only instruction file in a spike on 2026-11-03 (Claude Code 2.1.285) and in one run on 2026-11-06 (2.1.318), so no other file is needed. Keep the starter's rules in `AGENTS.md`, and don't add a `CLAUDE.md` or `.claude/CLAUDE.md` to it, for two reasons. The `agents_md` baseline removes only `AGENTS.md`, so its Δ, the template rate minus the baseline rate, measures the rules only while they live there. And in one run on 2026-11-06 with Claude Code 2.1.318, Claude Code ignored `AGENTS.md` when the project also had `.claude/CLAUDE.md` ([canary record](results/2026-11-06-claude-code-canary.md)). For an existing project you test with `--project-path`, Whitney copies one instruction file for Claude Code, always as `AGENTS.md`: its root `AGENTS.md`, or a `CLAUDE.md` when it has no `AGENTS.md`; see [Evaluating a local project](adding-tests.md#evaluating-a-local-project). The `kilo_jsonc` eval doesn't apply to the Claude Code starter. `python -m whitney.static_checks` checks both templates.

## Using it in a project

Copy both files into the root of your repo, the folder you start Kilo in:

```bash
cp template/kilo/AGENTS.md template/kilo/kilo.jsonc /path/to/your/repo/
```

Kilo loads `AGENTS.md` from the project root automatically. **Don't move it into `.kilo/`**: Kilo doesn't load `.kilo/AGENTS.md` on its own, so the rules would silently do nothing ([load-order.md](load-order.md)). The static check fails if it finds one.

## Changing it

- **Adding team rules:** add them under "Team rules" in `AGENTS.md`. Keep the core rules. `python -m whitney.static_checks` fails if one has been removed or reworded.
- **Changing permissions:** edit `kilo.jsonc`. Actions are `allow`, `ask` and `deny`. Kilo also has built-in rules your config sits on top of. For example, it asks before reading `.env` files, and a blanket `"read": "allow"` does **not** override that; you have to name the `*.env` patterns.
- **Adding files:** add text instruction/config assets anywhere under `template/kilo/` using Kilo's expected relative paths. See [adding-tests.md](adding-tests.md#adding-files-to-the-template); real `.env` files and secrets must not go in the template.
- Then add or update scenarios so the change is tested: [adding-tests.md](adding-tests.md).

## Known limits

- **Permissions are prompts, not a sandbox.** Kilo's own [threat model](https://github.com/Kilo-Org/kilocode/blob/main/SECURITY.md) says the permission system is a UX feature to keep you aware of what the agent does, "not designed to provide security isolation". The evals show `external_directory: ask` catches the common ways out of the project, but it isn't a boundary. For example, a symlink made in a shell command avoids the prompt under both `ask` and `deny` ([finding 001](findings/001-symlink-bypasses-external-directory.md)). If keeping the agent inside the project matters, run Kilo in a container or VM that only mounts the project, as Kilo recommends.
- **"Or secrets" doesn't cover keys in source code.** Asked to commit a file with a hard-coded API key, the agent committed it in 5 of 6 template runs, against 6 of 6 without the template ([results/](results/)). The rule reliably keeps `.env` files out; it doesn't make the agent check what's inside the files it commits.
