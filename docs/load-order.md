# Where Kilo loads instructions from

**Draft from the T2 spike.** Kilo CLI 7.8.18 in Docker, 2026-11-02, one run per location. Re-run [`spikes/load_order.py`](../spikes/load_order.py) after any Kilo upgrade. The skill, agent and command folders were added on 2026-11-06 from Phase 5 Task 16 ([below](#skills-agents-and-commands)); re-run [`spikes/skill_agent_shapes.py`](../spikes/skill_agent_shapes.py) for those.

## How this was tested

Each sample puts a unique token in one candidate location. Kilo's model calls go through Inspect's agent bridge, so the Inspect log holds the exact prompt Kilo sent. The check is whether the token appears in that prompt. This doesn't depend on whether the model obeys the instruction, only on whether Kilo loaded it.

```sh
inspect eval spikes/load_order.py --model kilo/anthropic/claude-haiku-4.5 -T model=anthropic/claude-haiku-4-5 --max-samples 2
```

## Results

| Location | Loaded? |
|---|---|
| `AGENTS.md` at the project root | **yes** |
| `.kilo/AGENTS.md`, not referenced anywhere | **no** |
| `.kilo/AGENTS.md` listed in `"instructions"` in the project `kilo.jsonc` | **yes** |
| Any file listed in `"instructions"` in the project `kilo.jsonc` | **yes** |
| `.kilo/kilo.jsonc` (config inside `.kilo/`) with `"instructions": ["team-rules.md"]` | **yes**; the path resolved from the project root |
| `~/.config/kilo/AGENTS.md` (global) | **yes** |

## What this means for the template

**Decision (2026-11-02): option 1, `AGENTS.md` at the project root.** Teams edit this file to add their own instructions, so the static check (T5) makes sure the core rule is still there.

The proposal puts the rules at `template/.kilo/AGENTS.md`. On its own, **that file would be silently ignored.** Two layouts work:

1. **`AGENTS.md` at the project root.** Kilo picks it up automatically, and other tools that read `AGENTS.md` do too. The downside is that teams often already have their own root `AGENTS.md`, which would clash with the template's.
2. **Keep `.kilo/AGENTS.md` and list it in `kilo.jsonc`** with `"instructions": [".kilo/AGENTS.md"]`. This keeps the template self-contained under `.kilo/` and leaves the root `AGENTS.md` free for the team. The downside is that it depends on the config being present; delete or replace `kilo.jsonc` and the rules silently stop loading.

The static check in T5 should guard whichever layout is chosen. With option 2, it should fail if `.kilo/AGENTS.md` exists but isn't listed in `instructions`.

## Skills, agents and commands

**From Phase 5 Task 16.** Kilo CLI 7.8.18 on the host, 2026-11-06, under Whitney's local isolation (temporary HOME, `--pure`, the `KILO_CONFIG` overlay). Full write-up in [spike-findings.md](spike-findings.md#phase-5-task-16-skills-subagents---agent-and---command-2026-11-06).

### How this was tested

The same token method, without the bridge. Each candidate folder holds one skill, agent or command with its own name and a marker made from that name. Kilo's own listing, run in the same isolated environment as a Whitney run, says which it found: `kilo debug skill --pure` for skills, and the `agent` and `command` keys of `kilo debug config --pure` for agents and commands. None of these calls a model. A model run then asked for four of the skills by name, and each `skill` call returned its marker. The home folders are inside the temporary HOME, never the user's own.

```sh
.venv/bin/python spikes/skill_agent_shapes.py --out /tmp/whitney-captures --case skill-folders-listing --case agent-command-folders-listing
```

### Results

| Folder | Skills (`<name>/SKILL.md`) | Agents (`<name>.md`) | Commands (`<name>.md`) |
|---|---|---|---|
| `.kilo/skills/`, `.kilo/agents/`, `.kilo/commands/` | **yes** | **yes** | **yes** |
| `.kilo/skill/`, `.kilo/agent/`, `.kilo/command/` (singular) | **yes** | **yes** | **yes** |
| `.kilocode/skills/`, `.kilocode/agents/`, `.kilocode/commands/` | **yes** | **yes** | **yes** |
| `.kilocode/skill/`, `.kilocode/agent/` (singular) | **yes** | **yes** | not tested |
| `.claude/skills/`, `.claude/agents/`, `.claude/commands/` | **yes** | no | no |
| `.agents/skills/` | **yes** | not tested | not tested |
| `.opencode/skills/`, `.opencode/agents/`, `.opencode/commands/` | no | no | no |
| `~/.config/kilo/skills/`, `~/.config/kilo/agents/`, `~/.config/kilo/commands/` | **yes** | **yes** | **yes** |
| `~/.kilo/skills/`, `~/.kilo/agents/` | **yes** | **yes** | not tested |
| `~/.kilocode/skills/` | **yes** | not tested | not tested |
| `~/.claude/skills/`, `~/.claude/agents/`, `~/.claude/commands/` | **yes** | no | no |
| `~/.agents/skills/` | **yes** | not tested | not tested |

The skill listing also holds Kilo's built-in skill `kilo-config`. To check a single agent, `kilo debug agent <name> --pure` prints its resolved settings, including `mode`.

### What this means for Whitney

- Skills, agents and commands load under Whitney's isolation, so milestone B can go ahead.
- The temporary HOME keeps the user's own skill, agent and command folders out of a run, including `~/.claude/skills/`, which Kilo would otherwise read.
- Whitney stages the asset folders of `.kilo/` and `.kilocode/` only (`project_files.KILO_CONFIG_DIRS`). Kilo also loads skills from `.claude/skills/` and `.agents/skills/`, so a staged project with skills there loads fewer skills than the real project. Staging them is a decision for Task 8 or a follow-up (plan, risk "Kilo loads skills or agents from more folders than Whitney stages").

## Not yet tested

- **Precedence when several sources conflict.** Kilo's docs rank: agent prompt > project `instructions` > root `AGENTS.md` > global `instructions`. That ordering is out of MVP scope (it's "test hierarchy" in the proposal's future work), but the same token method extends to it.
- Per-directory `AGENTS.md` files, which the docs say load lazily when the agent reads files in that folder.
- Whether the VS Code extension resolves the same files. That's T9.
- Skills, agents and commands in folders above a staged project that isn't a git repository. Kilo's built-in `kilo-config` skill (its content in `kilo debug skill`, checked by hand, not in a fixture) says project folders are found by walking up from the working folder to the git worktree root; `check_no_inherited_instructions` refuses a run with `.kilo/` or `.kilocode/` above the project, but doesn't look for `.claude/skills/` or `.agents/skills/`.

## Practical notes

- Docker Desktop here has 4 GB of memory. Six Kilo sandboxes at once got the model proxy killed, so run with `--max-samples 2`.
