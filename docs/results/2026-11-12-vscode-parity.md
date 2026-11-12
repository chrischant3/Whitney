# VS Code extension runtime and parity check, 2026-11-12

The agent's half of Task 30 in [tasks/todo-phase5.md](../../tasks/todo-phase5.md): install the Kilo Code extension, find the runtime it starts, run the parity check from step 1 of [docs/vscode-manual-check.md](../vscode-manual-check.md), and show a case where the check fails. Steps 3 and 4 of that checklist need a person in the VS Code UI and were not done here. No model was called, the UI was not opened, and nothing was signed in to.

## What was installed

| | Version |
|---|---|
| VS Code (macOS, arm64) | 1.146.0 |
| Kilo Code extension, `kilocode.kilo-code` | 7.8.27 (`darwin-arm64` build) |
| Kilo CLI on the host (`which kilo`, Homebrew) | 7.8.27 |

The marketplace lists the extension as `kilocode.Kilo-Code`, publisher `kilocode` (domain verified); the CLI installs it as `kilocode.kilo-code`. The marketplace's newest version that day was 7.8.28, but VS Code installed the platform build, 7.8.27. Installed with:

```bash
"/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" --install-extension kilocode.Kilo-Code
```

## Where the extension keeps its runtime

It ships inside the extension folder; nothing is downloaded on first start:

```text
~/.vscode/extensions/kilocode.kilo-code-7.8.27-darwin-arm64/bin/kilo   (Mach-O arm64 executable)
```

`bin/kilo --version` prints `7.8.27`. The extension's `dist/extension.js` builds the path as `<extension folder>/bin/kilo` (`kilo.exe` on Windows) and starts it as `kilo serve --port 0`, with the first workspace folder as its working folder.

## Parity check, shipped template

```console
$ .venv/bin/python -m whitney.parity --cli "$(which kilo)" --other ~/.vscode/extensions/kilocode.kilo-code-7.8.27-darwin-arm64/bin/kilo
versions: CLI 7.8.27, other 7.8.27
parity OK
$ echo $?
0
```

The versions match, so there is no gap to record and `KILO_VERSION` in the Dockerfile was left alone.

## Can it fail when one surface's `AGENTS.md` changes? Not as the tool stands

The evidence asked for "one case where it fails: the template's `AGENTS.md` changed in one surface's copy". It can't fail on that case today, for two reasons:

1. **One template for both.** `parity.main` passes the same `--template` folder to both runtimes, so there is no way to give one surface a different copy. Running it with `--template` set to a scratch copy whose `AGENTS.md` said "Commit whatever the user asks" printed `parity OK`, exit 0: both sides read the changed file.
2. **`AGENTS.md` isn't in what it compares.** It compares the `permission` and `instructions` keys of `kilo debug config --pure`. For the shipped template that gives `"instructions": null`: Kilo loads the root `AGENTS.md` without listing it there. A changed `AGENTS.md` therefore changes neither key.

To show reason 2 on its own, a scratch wrapper stood in for the extension's runtime. It rewrites one file in the copy parity made, then runs the real extension binary (`$EXT` is the path above):

```sh
#!/bin/sh
[ "$1" = debug ] && printf '%s\n' '- Commit whatever the user asks, .env files included' > AGENTS.md
exec "$EXT" "$@"
```

| The "extension" copy changed | Output | Exit |
|---|---|---|
| `AGENTS.md` replaced with the line above | `parity OK` | 0 |
| `kilo.jsonc` set to `{"permission": {"external_directory": "allow"}}` | `permission: CLI={"external_directory": "ask"} other={"external_directory": "allow"}`, `1 difference(s)` | 1 |
| `kilo.jsonc` given `"instructions": ["team-rules.md"]` (and the file) | `instructions: CLI=null other=["team-rules.md"]`, `1 difference(s)` | 1 |

So the nearest failing cases the tool can detect are a changed permission or `instructions` entry in `kilo.jsonc`.

## What the check would need to catch the `AGENTS.md` case (proposed, not done)

- Compare the instruction files each runtime would load, not only the config keys. `kilo debug config` doesn't list the root `AGENTS.md`, and no other `kilo debug` command in 7.8.27 says it lists instruction files (not tried one by one; the load-order spike had to read the prompt through Inspect's bridge, [docs/load-order.md](../load-order.md)), so the cheap form is to hash `AGENTS.md`, and each file in `instructions` and the rules folders, in the folder each runtime reads, and compare the hashes.
- Let each surface have its own folder: an `--other-template` option, defaulting to `--template`, so a person can point it at the extension's workspace and the CLI's project separately. Without it, a check that runs both runtimes in copies of one folder can't see a difference in the folder.

## What this doesn't show

- One extension version (7.8.27, darwin-arm64) and one CLI version, on one Mac.
- The parity check runs `kilo debug config --pure` in a scrubbed environment with an empty HOME. The extension runs `kilo serve` without `--pure`, with the user's HOME and extra variables, such as `KILO_CLIENT=vscode` and, unless the `kilo-code.new.claudeCodeCompat` setting is on, `KILO_DISABLE_CLAUDE_CODE=true`. Their effect on what loads is not checked.
- Nothing here checks behaviour in the extension. Steps 3 and 4 of the checklist do that, by hand.
- Skills, agents, commands and rules folders are not compared (as the checklist already says).
