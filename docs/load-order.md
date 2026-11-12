# Where Kilo loads instructions from

**Draft from the T2 spike.** Kilo CLI 7.8.18 in Docker, 2026-11-02, one run per location. Re-run [`spikes/load_order.py`](../spikes/load_order.py) after any Kilo upgrade. The skill, agent and command folders were added on 2026-11-06 from Phase 5 Task 16 ([below](#skills-agents-and-commands)); re-run [`spikes/skill_agent_shapes.py`](../spikes/skill_agent_shapes.py) for those. The rules folders, skills above the project and `instructions` globs were added the same day from Phase 5 Task 8 ([below](#rules-skill-folders-and-instructions-globs)).

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
- Whitney stages the asset folders of `.kilo/` and `.kilocode/`, and since Phase 5 Task 8 the skills in `.claude/skills/` and `.agents/skills/` too ([below](#rules-skill-folders-and-instructions-globs)).

## Rules, skill folders and instructions globs

**From Phase 5 Task 8.** Kilo CLI 7.8.18 on the host, 2026-11-06. Each check ran in a scratch project with a throwaway HOME, so the user's own Kilo folders stayed out, and with `--pure`. None of these commands calls a model.

### How this was tested

`kilo debug config --pure` prints the resolved config. Its `instructions` key lists every rules file Kilo will load, as absolute paths, so it shows which rules files Kilo found. `kilo debug skill --pure` lists every skill Kilo found, with its `location`. For each case the scratch project held one marker file per candidate location, and the commands were run from the project folder:

```sh
env -i PATH=/opt/homebrew/bin:/usr/bin:/bin HOME=$SCRATCH/home XDG_CONFIG_HOME=$SCRATCH/home/.config \
  XDG_DATA_HOME=$SCRATCH/home/.local/share XDG_CACHE_HOME=$SCRATCH/home/.cache XDG_STATE_HOME=$SCRATCH/home/.local/state \
  kilo debug config --pure | jq '.instructions'
# the same with `kilo debug skill --pure | jq '.[].location'`
```

### Rules

| File in the project | Loaded? |
|---|---|
| `.kilo/rules/<name>.md`, `.kilocode/rules/<name>.md` | **yes**, without any `instructions` entry |
| `.kilocode/rules/.hidden.md` | **yes** |
| `.kilo/rules/nested/<name>.md`, `.kilocode/rules/nested/<name>.md` | no |
| `.kilocode/rules/notes.txt`, `.kilo/rules/K.MD` | no: only names ending in lowercase `.md` |
| `.kilocode/rules/linked.md`, a symlink to a file | no |
| `.kilo/rules/same.md` and `.kilocode/rules/same.md` | only the `.kilo/` one: Kilo keeps the first file of each name |
| `.kilo/rules-code/`, `.kilocode/rules-code/`, `.kilocode/rules-architect/` (`*.md` in them) | **yes**, all of them, whatever the agent |
| `.kilocoderules`, `.kilocoderules-code` (files at the project root) | **yes** |
| `.kilo/rules/`, `.kilocode/rules/` in a folder above the project | no, in each of the three layouts below |

Kilo's bundled code gives the full list: the folders `.kilo/rules` and `.kilocode/rules`, a `rules-<mode>` folder in each for the modes `code`, `architect`, `ask`, `debug` and `orchestrator`, and the files `.kilocoderules` and `.kilocoderules-<mode>`, all in the working folder only. The same code also turns workflows into commands and `.kilocodemodes` into agents ([below](#workflows-and-custom-modes)).

**What Whitney does:** the Kilo loader stages every one of these rules files that Kilo loads (`project_files.KILO_RULE_DIRS` and `KILO_LEGACY_RULE_FILES`), under the same safety rules as everything else. It stages both `same.md` files, and Kilo, reading the staged copy, again keeps only the `.kilo/` one.

### Workflows and custom modes

Read from `kilo debug config --pure`, keys `command` and `agent`:

| File in the project | What Kilo makes of it |
|---|---|
| `.kilo/workflows/<name>.md`, `.kilocode/workflows/<name>.md` | a command named `<name>`, the file's text as its template |
| `.kilo/workflows/.hidden.md` | a command, `.hidden` |
| `.kilo/workflows/nested/<name>.md`, `.kilo/workflows/notes.txt`, `.kilo/workflows/UP.MD` | nothing |
| the same name in both folders | only the `.kilo/` one |
| `.kilocodemodes` (YAML, `customModes:` with `slug`, `name`, `roleDefinition`, `groups`) | an agent per mode, named by its `slug`, `mode: primary`, `roleDefinition` as its prompt, and permissions from `groups` (`groups: [read]` gave `read: allow`, `edit`, `bash` and `mcp` `deny`) |

**What Whitney does:** the Kilo loader stages the `.md` files directly in both workflows folders and a root `.kilocodemodes` (`KILO_WORKFLOW_DIRS`, `KILO_MODES_FILE`), under the same safety rules.

### `skills.paths`

With `"skills": {"paths": ["./my-skills", "<a folder outside the project>"]}` in `kilo.jsonc`, `kilo debug skill --pure` listed the skill in `./my-skills/`. The outside folder's skill wasn't listed in this run, but the key can name any folder on the machine. **What Whitney does:** the Kilo loader strips `skills.paths` from each staged config, as it strips the connection keys, and the report's "Not Staged" table names it as `<config>: skills.paths`.

### Skill folders

`.claude/skills/<name>/SKILL.md` and `.agents/skills/<name>/SKILL.md` in the project are listed by `kilo debug skill`, as in Task 16. In a folder above the project:

| Layout | Skills from `.claude/skills/` and `.agents/skills/` above? |
|---|---|
| The project is a git repository | no |
| No git repository anywhere | no |
| The project is a plain folder inside a git repository that starts above it | **yes**, from each folder up to the repository root |
| The same, with `GIT_CEILING_DIRECTORIES` set to the folder above the project, as Whitney sets it | **yes**: Kilo doesn't use it |

**What Whitney does:** the Kilo loader stages both skill folders. A scenario whose setup has no `git_init` runs in a project that isn't a repository, so `check_no_inherited_instructions` refuses a run with `.claude/skills/` or `.agents/skills/` in a folder above the project when that folder, or one above it, holds a `.git`. Without one, Kilo has no worktree root to walk up to and reads none of them (the second row above), and refusing would only stop users whose TMPDIR is under a HOME holding `~/.claude/skills/`. `.kilo/`, `.kilocode/` and `.claude/CLAUDE.md` above the project are still refused wherever they are.

### Instructions globs

`kilo debug config` prints `instructions` patterns as written, not expanded, so it can't show which files a pattern matches. Kilo's bundled code shows how it expands them: a relative pattern is matched with node-glob, with the options `dot: true`, `follow: false` and `nodir: true`, in the working folder and each folder above it up to the git worktree root. A staged project is its own root, so only the project counts. To see what that matches, the same patterns ran through node-glob 13.0.6 (the copy bundled with npm, called with those options) and through Python's `Path.glob`, on one tree:

| Pattern | Kilo's glob | `Path.glob` before Task 8 |
|---|---|---|
| `rules/*.md` | `.md` files directly in `rules/`, dot files included | the same, but case-sensitive |
| `rules/**/*.md` | every `.md` file under `rules/`, at any depth, `rules/a.md` included | the same, but case-sensitive |
| `**/*.md` | every `.md` file in the project, in dot folders too | the same, but case-sensitive |
| `rules/**` | every file under `rules/` | the same on Python 3.13 and later; folders only on 3.11 and 3.12 |
| `rules/{a,x}.*` | `rules/a.md` and `rules/x.txt` | nothing: no brace expansion |
| `RULES/*.MD` on macOS | `rules/a.md` and the other `.md` files: names match whatever their case | `rules/UPPER.MD` only |
| `rules/!(a).md`, `rules/{1..3}.md` | extglob and range syntax | nothing |
| `**` through a symlinked folder | not followed | not followed |

On Linux, node-glob matches case-sensitively. **What Whitney does:** it expands `*`, `?`, `**`, `[...]`, `[!...]` and `{a,b}` as node-glob does, with case-blind names on macOS and Windows (`project_files._instruction_matches`), and refuses a pattern with any other glob syntax, naming it. The tests in `tests/test_project_files.py` pin each row. Whitney still leaves out what its safety rules leave out (`.env` files, symlinks, `node_modules/` and the other excluded folders), and the evals path. Kilo reads those, but the staged copy never holds them.

## Not yet tested

- **Precedence when several sources conflict.** Kilo's docs rank: agent prompt > project `instructions` > root `AGENTS.md` > global `instructions`. That ordering is out of MVP scope (it's "test hierarchy" in the proposal's future work), but the same token method extends to it.
- Per-directory `AGENTS.md` files, which the docs say load lazily when the agent reads files in that folder.
- Whether the VS Code extension resolves the same files. That's T9.
- Agents and commands in folders above a staged project. Skills above it were checked in Task 8 ([above](#rules-skill-folders-and-instructions-globs)); `check_no_inherited_instructions` refuses a run with `.kilo/` or `.kilocode/` above the project, and with `.claude/skills/` or `.agents/skills/` above it inside a git repository ([above](#rules-skill-folders-and-instructions-globs)).

## Claude Code

Claude Code's own [memory docs](https://code.claude.com/docs/en/memory.md) (read 2026-11-11) give its order. It loads the root `CLAUDE.md`, `.claude/CLAUDE.md` and `CLAUDE.local.md` together, from the working folder and every folder above it. It reads `AGENTS.md` and `.claude/AGENTS.md` only when none of those three is there. A spike on 2026-11-11 with Claude Code 2.1.322 agreed (`docs/spike-findings.md`).

**What Whitney does:** the `claude-code` loader copies the files Claude Code would read, under their own names, but never `CLAUDE.local.md` ([adding-tests.md](adding-tests.md)). Whether `AGENTS.md` is copied depends only on the root `CLAUDE.md` and `.claude/CLAUDE.md`: `CLAUDE.local.md` is personal and not committed, so a run in CI or on another machine wouldn't have it. `check_no_inherited_instructions` refuses a run with any of these files in a folder above the project.

### Skills and agents

Claude Code 2.1.322 offers the skills in the project's `.claude/skills/` and the agents in its `.claude/agents/` (`docs/spike-findings.md`, "Phase 5 Task 36"):

- A skill is a folder directly in `.claude/skills/`, offered under the folder's name. The `name` in its `SKILL.md` doesn't change that.
- An agent is a `.md` file anywhere under `.claude/agents/`, offered under the `name` in its frontmatter. A file with no `name` isn't offered.
- With `.claude/skills/` or `.claude/agents/` in the folder above the project, their skills and agents are offered only when the project isn't a git repository.

**What Whitney does:** the `claude-code` loader copies both folders. Validation refuses an agent under test whose `name` isn't its file's name. A scenario whose setup has no `git_init` runs in a project that isn't a repository, so `check_no_inherited_instructions` refuses a Claude Code run with `.claude/skills/` or `.claude/agents/` above such a project.

## What each target stages, side by side

| In the project | `kilo` | `claude-code` |
|---|---|---|
| Root `AGENTS.md` | staged | staged only when there is no `CLAUDE.md` or `.claude/CLAUDE.md` |
| Root `CLAUDE.md` | staged. Whether Kilo reads it beside an `AGENTS.md` hasn't been tested here | staged, with `.claude/CLAUDE.md` |
| `kilo.jsonc`, `.kilo/rules/`, `.kilo/skills/`, `.kilo/agents/` | staged | not staged |
| `.claude/skills/` | staged | staged |
| `.claude/agents/` | not staged (Kilo doesn't read it, [above](#results-1)) | staged |
| `.claude/settings.json` | not staged | its `permissions` block only |
| Files a `CLAUDE.md` imports with `@path` | not staged | not staged |

[`examples/consumer`](../examples/consumer/) is laid out for both. Its skills are in `.kilo/skills/` for Kilo, with a copy in `.claude/skills/` for Claude Code; Kilo reads both. When two skills share a name, Kilo 7.8.27 loaded the `.kilo/` copy in each of the 4 runs of the recorded one-epoch run that loaded it, and the `.claude/` copy in the 2 control runs where the `.kilo/` copy was removed ([results](results/2026-11-12-claude-code-example.md#which-skill-copy-kilo-used)); for rules files and workflows Kilo keeps the `.kilo/` one too. The Kilo skill eval's baseline removes both copies, and on `kilo` its automatic trigger control removes every staged skill folder named `release-notes` in the folders Kilo reads ([adding-tests.md](adding-tests.md#testing-a-skill-or-an-agent)); re-run after that fix, the control failed both positive prompts, as it should ([results](results/2026-11-12-claude-code-example.md#the-kilo-trigger-control-re-run-after-the-fix)). Copies are matched by folder name; no Kilo run here has had a skill whose folder and `name` differ. Its `CLAUDE.md` repeats the rules of `AGENTS.md` and `.kilo/rules/data-handling.md` rather than importing them, and its Kilo baselines remove `CLAUDE.md` as well as the file under test, because the Kilo loader stages it.

## Practical notes

- Docker Desktop here has 4 GB of memory. Six Kilo sandboxes at once got the model proxy killed, so run with `--max-samples 2`.
