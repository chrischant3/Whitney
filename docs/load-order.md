# Where Kilo loads instructions from

**Draft from the T2 spike.** Kilo CLI 7.8.18 in Docker, 2026-11-02, one run per location. Re-run [`spikes/load_order.py`](../spikes/load_order.py) after any Kilo upgrade.

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

## Not yet tested

- **Precedence when several sources conflict.** Kilo's docs rank: agent prompt > project `instructions` > root `AGENTS.md` > global `instructions`. That ordering is out of MVP scope (it's "test hierarchy" in the proposal's future work), but the same token method extends to it.
- Per-directory `AGENTS.md` files, which the docs say load lazily when the agent reads files in that folder.
- Whether the VS Code extension resolves the same files. That's T9.

## Practical notes

- Docker Desktop here has 4 GB of memory. Six Kilo sandboxes at once got the model proxy killed, so run with `--max-samples 2`.
