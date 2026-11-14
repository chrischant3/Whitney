# Spike: running a team's hooks safely, shown in Docker

Run 1 `git push`-refusing hook on Claude Code and 1 `git push`-refusing plugin on Kilo, each in Whitney's existing no-network Docker sandboxes (`src/whitney/sandbox/claude-code/` and `src/whitney/sandbox/`), plus 1 probe per harness that tries to reach the login, the network and the model bridge. Then write the results, and the recommendations Christopher needs to decide Tasks 45 and 46, into `docs/spike-findings.md`. Local runs aren't repeated, because Task 40's section already shows that a local hook gets `ANTHROPIC_API_KEY` and that a local plugin runs inside Kilo's process, next to the login. Nothing under `src/` changes.

## Job story

When I'm about to build hook staging for Tasks 45 and 46, and a team's `PreToolUse` hook or `tool.execute.before` plugin is what stops `git push` in their real setup
I want to see, from real runs, where such a hook can run without reaching a login or the network, and what the run's stream records when it refuses
So I can decide where hooks may run, what they may reach and how the report names them, before any of it is built

## Acceptance criteria

- [ ] Given Claude Code in its Docker sandbox, with a project `.claude/settings.json` that holds only a `PreToolUse` command hook on `Bash` refusing `git push`, when the spike's script runs a prompt asking for `git push origin main` with `--include-hook-events` (the prompt made more pressing until the model attempts the push), then `docs/spike-findings.md` has a Claude Code section showing all of these:
  - the hook's `hook_started` and `hook_response` events
  - the refused call
  - the tool result text starting `PreToolUse:Bash hook error:` and its `permission_denials` entry, set beside the `settings-deny-held` fixture's rule refusal so the field or text that tells them apart is named
  - any runs where the push wasn't attempted, listed

  The stream is saved under `tests/data/claude-code-events/` with a `.meta.json`.
- [ ] Given Kilo in its Docker sandbox, run without `--pure`, with a `.kilo/plugin/` file whose `tool.execute.before` throws for `git push`, when the spike's script runs the same prompt (made more pressing until the push is attempted), then the Kilo section shows all of these:
  - a file the plugin wrote in the project, showing it ran
  - the push refused, as a `tool_use` event with `state.status` `error` and the thrown message
  - the exit code
  - any runs where the push wasn't attempted, listed

  The stream is saved under `tests/data/kilo-events/` with a `.meta.json`. If the plugin can't load because `@kilocode/plugin` can't be installed with no network, the section records that failure and its message. It then shows the run again with the dependency made available, either preinstalled in a spike-only image or as a vendored copy, and says which it used.
- [ ] Given each sandbox, when a test-only hook (Claude Code) and a test-only plugin (Kilo) each try the 4 things below, then each section records exactly what each attempt got, including whether the key in the hook's environment is the placeholder `sk-none`:
  - read the login: the host's login paths, and `ANTHROPIC_API_KEY` or Kilo's `auth.json`
  - reach an outside host
  - write outside the project
  - call Inspect's bridge on localhost: `ANTHROPIC_BASE_URL` for Claude Code, the provider `baseURL` in Kilo's config
- [ ] Given I read the 2 sections, then each has a table of that harness's hook events, saying what each can do to a run and whether its effect shows in the stream, with each row marked "from a run" or "from the docs". Claude Code's table covers its hook events. Kilo's covers `tool.execute.before`, `tool.execute.after`, `permission.ask`, `chat.message`, `experimental.chat.system.transform` and the `event` bus.
- [ ] Given the Kilo runs, when I read the Kilo section, then it lists the files Kilo wrote into `.kilo/` on the image's Kilo version, beside Task 40's list for 7.8.27. It also says whether the project snapshot (`evidence.snapshot`, which leaves out only `.git/`) would report them as changes the agent made.
- [ ] Given I read the 2 sections, then each gives the harness version, the docs read (URL and date), every run (its command, its exit code and where its stream is kept) and a "Not shown" list. Each also gives a recommendation for Christopher to decide on each of these:
  - where hooks run: only in Docker, refused locally, or locally behind a flag that says what it allows
  - what a hook can reach: no login, no network beyond the model's, and only the project, with what the bridge probe means for "beyond the model's"
  - how the report names each staged hook or plugin, and whether its effect is visible in the evidence
  - whether the snapshot ignores the `.kilo/` files or the run removes them
  - for Kilo, whether a run drops `--pure` only when a plugin is staged, and only in Docker
- [ ] Given the spike is done, when I run `python scripts/check.py task` with the new fixtures in place, then it passes, and `git diff main --stat` shows no change under `src/`

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `docs/spike-findings.md`: "Phase 5 Task 40" (around line 485) has the hook and plugin scripts, the local findings this slice builds on, and the list of what staging would need. "Phase 5 Task 36a" (around line 782) is the section shape to copy.
- `spikes/claude_code_docker.py`: the closest prior art. It runs Claude Code in the sandbox through Inspect's bridge, with Whitney's flags, and keeps the stream in the log's store.
- `src/whitney/sandbox/compose.yaml`: the Kilo sandbox. It has no network and is pinned to Kilo 7.8.18, while Task 40's plugin runs used 7.8.27.
- `src/whitney/agent.py`: the Docker branch (`if execution == "docker":`, around line 188), and the `--pure` command (line 661) that a spike runner has to change for Kilo.
- `src/whitney/claude_code.py`: `_outcome` (around line 224) marks any denial `ended_early`, so a hook's refusal can't yet be told from a rule's.
- `tests/data/claude-code-events/`: the `settings-deny-held` fixture and Task 40's `hook-pretooluse-deny` fixture, the rule refusal and the hook refusal to compare. Run the nearest tests with `.venv/bin/python -m pytest -q tests/test_claude_code_docker.py tests/test_checks_permissions_claude_code.py`.

## Assumptions

- May the spike spend money on model calls? Yes, up to about a dollar of Haiku (`claude-haiku-4-5`, with Kilo given the matching Anthropic name through the bridge). The implementing session still asks Christopher before the first model call, as the task's "Cost" line requires.
- Which Kilo version? The image's pinned 7.8.18, unchanged. Task 40's 7.8.27 results are cited, not rerun. Bumping the image is its own change.
- Does a spike-only image count as "building"? No. A Dockerfile and compose file under `spikes/` that add `@kilocode/plugin` are spike code, as `spikes/claude_docker/` was for Task 36a. The shipped image isn't changed.
- Are local runs needed to recommend "refused locally"? No. Task 40 already recorded the key in a local hook's environment, and the plugin running in Kilo's process.
- Which hooks get runs? Only `PreToolUse` on Claude Code and `tool.execute.before` on Kilo. The rest of each table comes from the docs and Task 40's runs, marked that way.
- Who records Christopher's decisions? Not this slice. The spike ends with recommendations marked for him to decide. His dated decisions go under Task 44 in `tasks/plan-later.md` once he gives them.

## Owed

- Task 44's last verification item, "Christopher's decisions, recorded here, dated". This slice ends with recommendations, so Task 44's box stays open until he decides, and the Task 45 and 46 specs stay guesses until then.
