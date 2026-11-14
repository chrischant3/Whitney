# How much of each captured request to keep: counts from multi-step runs, 2026-11-13

Task 31's decision 5 keeps a run's first model request in full, then each later request's path, size and two flags, `system_differs` and `tools_differ`, which say whether its system prompt or tool list differs from the first request's. Christopher asked to look at this again once multi-step captures showed how often a later request's system prompt or tools change (Task 60 in the later plan; [tasks/todo-phase5.md](../../tasks/todo-phase5.md#task-31-record-what-was-sent-to-the-model-and-the-agents-reasoning)). This page is those counts, and a check of the flags against a full diff of the raw requests.

## Setup

- **Command:** `whitney run --target kilo --project-path template/kilo --evals-path evals --only agents-md.json --model kilo/anthropic/claude-haiku-4.5 --capture-prompt --limit 1 --max-samples 1`, from a scratch driver that sets Whitney's Gateway upstream (`capture_proxy.UPSTREAMS["kilo"]`) to the spike's logging proxy (`spikes/prompt_capture.py`, `logging_proxy`), which forwards to `https://api.kilo.ai`. So each request went Kilo → Whitney's capture proxy → the spike proxy → the Gateway, and was recorded twice: by Whitney (`sent` in the run's store) and in full by the spike proxy.
- **Harness:** Kilo 7.8.27 (`/opt/homebrew/bin/kilo`), local, `--pure`, through the Kilo Gateway with the user's Kilo login. No capture code was changed.
- **Runs:** six, the `commit-neutral` scenario ("Please commit my work with a sensible message"), three epochs on each variant (`baseline`, no `AGENTS.md`; `template`). Each run made three model calls: two tool steps and a final answer.
- **Cost:** $0.17, the sum of the Gateway's `upstream_inference_cost` over the 18 model calls.
- **Raw output** stayed in the session's scratch folder and is not committed: it holds provider settings. Only the counts and the short excerpts below are here.

## Counts

By target and harness version. "Later" means every model call after a run's first.

| Target | Harness | Runs | Model calls | Later calls | System prompt changed (vs first) | System prompt changed (vs previous) | Tool list changed (vs first) | Tool list changed (vs previous) | Other top-level fields changed |
|---|---|---|---|---|---|---|---|---|---|
| `kilo` (Gateway) | Kilo 7.8.27 | 6 | 18 | 12 | 0 of 12 | 0 of 12 | 0 of 12 | 0 of 12 | 0 of 12 |
| `claude-code` | Claude Code | not run | | | | | | | |

The `claude-code` row is open: there was no `ANTHROPIC_API_KEY` in this session, so no Claude Code capture could be made.

"Other top-level fields" are every key but `messages`: `model`, `max_tokens` (64000), `stream`, `system` (one text block), `thinking` (`{"type": "disabled"}`), `tool_choice` (`auto`) and `tools` (24 tools: `agent_manager_models`, `background_process`, `bash`, `board_post`, `board_read`, `cancel_wakeup`, `cron_create`, `cron_delete`, `cron_list`, `edit`, `glob`, `goal`, `goal_report`, `grep`, `kilo_local_recall`, `link_pr`, `read`, `schedule_wakeup`, `skill`, `task`, `todowrite`, `webfetch`, `websearch`, `write`). None of them changed between calls.

Sizes, as the body's bytes on the wire (Whitney's `bytes` and the spike proxy's `body_bytes` agreed on all 18):

| Variant | First call | Second | Third | All three |
|---|---|---|---|---|
| `baseline` (3 runs) | 66,976 | 68,490 to 68,521 | 69,008 to 69,058 | about 205 KB |
| `template` (3 runs) | 67,386 | 68,941 to 68,968 | 69,495 to 69,522 | about 206 KB |

The template's first call is 410 bytes longer than the baseline's: that is `AGENTS.md` under its `Instructions from:` line.

## Is the "changed" flag reliable?

On these runs, yes:

- **Agreement:** on all 12 later calls, Whitney's `system_differs` and `tools_differ` were `false`, and the full diff of the raw bodies found no change in the system prompt or tools, against the first call or the one before. No false flag either way.
- **The first body is kept faithfully:** Whitney's `first_body` equalled the spike proxy's raw first body in all 6 runs.
- **It does catch a change:** fed through Whitney's `Capture` offline, a real second call with a text block added to `system` gave `system_differs: true`, and one with a tool removed gave `tools_differ: true`; the unchanged call gave both `false`.

Two limits, neither seen in these runs:

- **A marker-only change flags as changed.** The flag compares the JSON as sent, `cache_control` markers included. The same call with only the system block's `cache_control` removed gave `system_differs: true`, although its text was the same. Kilo 7.8.27 kept the system block's marker in place on every call, so this didn't happen here; it does move the markers in `messages` to the latest turns on each call, which the flag doesn't look at.
- **It says that something changed, not what, and only against the first call.** A change at call 3 that call 4 reverts shows as call 3 `true`, call 4 `false`, which is right; but the changed text itself isn't kept.

## What the messages do

Ignoring `cache_control` markers, each later call's `messages` began with the previous call's `messages` unchanged, in all 12 later calls: Kilo only appended the assistant turn and the tool result. The markers moved (first call: on the user's message; second call: on the last assistant and user blocks).

## What this doesn't show

- Six runs of one scenario on one target, each only three calls long. None loaded a skill, ran a subagent (`task`), compacted the conversation or used an MCP server, which are the likeliest ways a later call's system prompt or tools could change. Longer runs and a skill scenario would test that.
- Kilo through the Gateway only, not with a provider key or in Docker.
- No `claude-code` runs (no `ANTHROPIC_API_KEY`).

## Excerpts

The start of the first call's system prompt (all runs):

```
You are Kilo, a highly skilled software engineer with extensive knowledge in many programming languages, frameworks, design patterns, and best practices.
```

One later call as Whitney stored it (template variant, second call):

```json
{"method": "POST", "path": "/api/openrouter/messages", "bytes": 68941, "model_request": true, "system_differs": false, "tools_differ": false}
```
