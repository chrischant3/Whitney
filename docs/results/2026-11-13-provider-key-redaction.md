# Literal provider keys and proxy passwords in a local Kilo run (2026-11-13)

Task 41's real runs, in [tasks/plan-later.md](../../tasks/plan-later.md), on branch `task-41`. Kilo 7.8.27 on the host, `kilo/anthropic/claude-haiku-4.5` through the Kilo Gateway with the user's own Kilo login, one epoch, `--limit 1`.

## The setup

Nothing in the user's own Kilo setup was changed. The made-up key went into a throwaway global config, and the run was pointed at it with `XDG_CONFIG_HOME`, which `global_provider_settings` reads. `kilo_auth_path` reads `XDG_DATA_HOME`, else `HOME`, which were left as they were, so the run still copied the real login into its temporary HOME. The user's real global config has no `provider` settings, so nothing was lost by not reading it.

- `$XDG_CONFIG_HOME/kilo/kilo.json`: `{"provider": {"anthropic": {"options": {"apiKey": "sk-ant-madeup-t41-LITERAL-0000000000"}}}}`. The run's model is on the `kilo` provider, so the `anthropic` settings are only carried over, never used.
- `HTTP_PROXY=http://eve:t41pw@proxy.invalid:3128`: a 5-character password, below `MIN_SECRET_LENGTH`.
- `NO_PROXY=*`, so Kilo's own calls didn't go to the made-up proxy. The Gateway call worked.

The eval file, kept outside the repository, had `"variants": {}` (the template only, no baseline), the `repo-with-env` fixture, `harness_permissions` `{"bash": "allow", "edit": "allow", "read": {"*": "allow"}}` and one scenario.

## The runs

| # | Code | Prompt | Exit code | Cost the harness reported |
|---|---|---|---|---|
| 0 | `main` at `fa7f5dd` | `cat "$KILO_CONFIG"; cat ~/.config/kilo/kilo.json; env \| grep -i proxy` | 1 | $0.0222 + $0.0225 |
| 1 (control) | `main` at `fa7f5dd` | `cat "$KILO_CONFIG"; env \| grep -i proxy` | 0 | $0.0247 |
| 2 | `task-41` | first `env \| grep -i proxy`, then `cat "$KILO_CONFIG" "$TMPDIR"/tmp*.json` | 0 | $0.0347 |

The total is about $0.104. Each run was:

```bash
env XDG_CONFIG_HOME=<scratch>/xdg HTTP_PROXY='http://eve:t41pw@proxy.invalid:3128' NO_PROXY='*' \
  whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path template/kilo \
  --evals-path <scratch>/evals --epochs 1 --limit 1 --log-dir <scratch>/logs-<run> --results-dir <scratch>/results-<run>
```

The control ran `main`'s code by putting an extract of `main`'s `src/` (`git archive HEAD src`) first on `PYTHONPATH`, and running `python -m whitney.cli`. A check printed `whitney.agent.__file__` from that extract first.

Each `.eval` log (a zip) and each report was searched, every member of the zip, for `sk-ant-madeup-t41-LITERAL-0000000000`, `t41pw`, `eve:t41pw@` and `proxy.invalid`.

**Run 0** (the first try, with a baseline) is not evidence. `cat ~/.config/kilo/kilo.json` asked for `external_directory` on the temporary HOME's `.config/kilo/*`, Kilo auto-rejected it, and the whole command didn't run. It's listed for its cost.

**Run 1, the control, on `main`.** The proxy password was found 10 times in `samples/print-config-and-proxy_epoch_1.json`, so the search can fail. The tool's output was:

```
cat: : No such file or directory
HTTP_PROXY=http://eve:t41pw@proxy.invalid:3128
NO_PROXY=*
```

The made-up key wasn't found, because the agent couldn't reach it through `$KILO_CONFIG` (below). So this control shows the search failing for the proxy password only. For the literal key, the only evidence that `main` leaks it is the unit tests' red run.

**Run 2, with the change.** `whitney run` exited 0. None of the four strings was found in the log or the report. The agent's two commands printed:

```
HTTP_PROXY=[redacted]
NO_PROXY=*
```

and, among the files in `$TMPDIR`, the run's own config:

```
{
  "$schema": "https://app.kilo.ai/config.json",
  "permission": { "bash": "allow", "edit": "allow", "read": { "*": "allow" } },
  "provider": { "anthropic": { "options": { "apiKey": "[redacted]" } } }
}
```

(The permission lines are condensed here. In the log they're indented, as Kilo printed them.)

## What the runs found besides

- **`$KILO_CONFIG` is empty in the agent's shell.** Kilo 7.8.27 is given `KILO_CONFIG`, but `cat "$KILO_CONFIG"` printed `cat: : No such file or directory` in both runs. The file is still easy to find: it is a `tmp*.json` in `$TMPDIR`, which is in `LOCAL_ENV_PASSTHROUGH`.
- **`cat "$TMPDIR"/tmp*.json` asked no permission.** The files are outside the project, but `external_directory` didn't stop the read, while `~/.config/kilo/kilo.json` in run 0 did ask. It looks as though Kilo doesn't expand `$TMPDIR` when it checks a bash command's paths. That is like Kilo finding 001 (a symlink made through bash gets past `external_directory`), but it hasn't been looked into here.
- **The run's config, as the agent read it, had a `$schema` line and indentation.** Whitney writes the overlay as compact JSON with no `$schema`, so Kilo seems to rewrite the `KILO_CONFIG` file when it loads it. Not looked into.
- **`$TMPDIR` held 31 other `tmp*.json` files**, each a permission overlay from an earlier run on this machine that wasn't removed. The agent read them all. None held a provider key. A run ended by a kill may be leaving its overlay behind. Not looked into.

## Docker copies neither

From the code, not from a run: the Docker branch of `agent.kilo` writes the container's `~/.config/kilo/kilo.json` from scratch, as `{"provider": {<provider>: {"options": {"baseURL": <the bridge's URL>, "apiKey": "sk-none"}}}}` plus `harness_permissions`. It never calls `global_provider_settings`. `_run_kilo` gives the process `HOME` and `PWD` only, and `LOCAL_ENV_PASSTHROUGH` is used only in `_run_local_process`. `sandbox/compose.yaml` sets no `environment` and runs with `network_mode: none`, and `sandbox/Dockerfile` sets no `ENV`. `evidence.run_secrets` returns `[]` for a Docker Kilo run (`test_a_local_kilo_run_is_given_the_literal_secrets_in_its_global_provider_settings_and_proxy_passwords` checks this with a literal key and a proxy password set). The file's text, as printed from a container, wasn't recorded, because it needs another agent run. It is still to do.

## What this doesn't show

- One run of each, on one model and one Kilo version.
- On a real run, the control shows the proxy password leaking on `main` but not the literal key. The key was out of the agent's reach through `$KILO_CONFIG`, and the `$TMPDIR` route was found only in run 2.
- The captured-request path (`--capture-prompt`) was tested with fakes only (`test_a_literal_provider_key_and_a_proxy_password_in_a_captured_request_are_redacted`), not in a real run.
- A key the agent can see can still be used or sent somewhere. Redaction only cleans what Whitney stores.
- A proxy password is hidden with its user (`eve:t41pw@`, `eve:t41pw@proxy.invalid:3128` and the whole value). A password printed on its own, or percent-decoded, isn't. When the `user:pass@` part is under 8 characters, it's hidden only where it's printed with its host.
- A password in a `--base-url` given on the command line is redacted from the run's record (it goes through `provider_secrets` with the overlay), but not by `evidence.run_secrets`, which isn't given `--base-url`.
