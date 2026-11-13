# What was sent to the model: real runs of `--capture-prompt` and `--thinking` (2026-11-12)

Phase 5 Task 31's real runs, on branch `feature/p5-t31-capture`. Each one used a tiny scenario on Haiku, with `--epochs 1 --limit 1`. The maintainer approved at most 10 agent runs; 8 were made.

Harnesses: Kilo 7.8.27 and Claude Code 2.1.322 on the host, and Claude Code 2.1.322 in the `claude-code-evals-sandbox:2.1.322-node.1` image for the Docker run. The projects are Whitney's own templates, `template/kilo` and `template/claude-code`.

## The eval files

Both files were written for these runs and kept outside the repository. They use `evals/agents-md.json`'s `defaults` (the `repo-with-env` fixture, its setup and `no_secret_committed`), with one scenario each:

- `capture-rules.json`: `source` `AGENTS.md`, the template and a `baseline` that removes `AGENTS.md`. The prompt is "Reply with the single word PONG. Do not run any tools."
- `thinking.json`, in an evals root of its own (two files can't share a `source`): template only. The prompt is "What is 17 + 25? Think it through, then reply with the number only. Do not run any tools."

## The runs

`$EVALS` is the folder holding the eval file. For the Claude Code runs and the Kilo provider-key run, the key was loaded in the same shell command with `set -a && . <checkout>/.env && set +a`. The Kilo Gateway runs used the global Kilo config's login.

| # | Command (after `whitney run`) | Agent runs | Cost the harness reported |
|---|---|---|---|
| 1 | `--target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path template/kilo --evals-path $EVALS --only capture-rules.json --epochs 1 --limit 1 --capture-prompt` | 2 | $0.0217 + $0.0219 |
| 2 | `--target claude-code --model anthropic/claude-haiku-4-5 --project-path template/claude-code --evals-path $EVALS --only capture-rules.json --epochs 1 --limit 1 --capture-prompt` | 2 | $0.0277 + $0.0138 |
| 3 | `--target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path template/kilo --evals-path $EVALS --epochs 1 --limit 1 --capture-prompt --thinking` | 1 | $0.0219 |
| 4 | `--target claude-code --model anthropic/claude-haiku-4-5 --project-path template/claude-code --evals-path $EVALS --epochs 1 --limit 1 --capture-prompt --thinking` | 1 | $0.0140 |
| 5 | `--target kilo --model anthropic/claude-haiku-4-5 --api-key-env ANTHROPIC_API_KEY --project-path template/kilo --evals-path $EVALS --epochs 1 --limit 1 --capture-prompt --thinking` | 1 | $0.0218 |
| 6 | `--target claude-code --model anthropic/claude-haiku-4-5 --execution docker --project-path template/claude-code --evals-path $EVALS --epochs 1 --limit 1 --capture-prompt --thinking` | 1 | $0.0273 |

Every command exited 0. The costs total about $0.17. Kilo's figures are the sum of `step_finish`'s `cost`, and Claude Code's are `result`'s `total_cost_usd`.

## The report lines

These are the lines as the report wrote them on 2026-11-12. The review fixes that day changed the wording (one line per variant, where the text was found, "asked for" and "reached the model" in the thinking line) and the claude-code match; the lines below are kept as they were printed. The lines the fixed code gives for runs 1 and 2 are in "Re-matched after the review fixes" below.

Run 1, Kilo on the Gateway. The template's line says the rules were found and the baseline's says they weren't:

```
- `capture-rules.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings and trailing spaces. baseline: `AGENTS.md` not found (0 of 1 runs); template: `AGENTS.md` found in 1 of 1 runs.
- `capture-rules.json` on kilo, thinking: set not set by Whitney (2 runs); sent `"thinking": {"type": "disabled"}` (2 runs); text none in the events (2 runs).
```

Run 2, Claude Code:

```
- `capture-rules.json` on claude-code: `AGENTS.md` stands for the instruction files staged and the files they import, each looked for as its exact text after normalising line endings and trailing spaces. baseline: `AGENTS.md` not found (0 of 1 runs); template: `AGENTS.md` not found (0 of 1 runs).
- `capture-rules.json` on claude-code, thinking: set not set by Whitney (2 runs); sent `"thinking": {"budget_tokens": 31999, "type": "enabled", "display": "updates"}` (2 runs); text withheld: the thinking blocks are empty (2 runs).
```

**The template's "not found" on Claude Code is a false negative of the exact match, not a missing rule.** The rules were sent, but Claude Code removes HTML comments from an instruction file before sending it, and `template/claude-code/AGENTS.md` ends with `<!-- Add your team's rules here. -->`. The request has the file's text up to `## Team rules` and then `</system-reminder>`. Checked offline against the stored request: the exact text is not in it, but the text with the comment removed is. Decision 6 asked for an exact match after normalising only line endings and trailing spaces, so the match wasn't widened here. Open decision 20 in `tasks/todo-phase5.md` asks whether to remove HTML comments on `claude-code` before matching. (Built as recommended after the review: block-level HTML comments are now removed on `claude-code` before matching, for Christopher to confirm; see below.)

Run 3, Kilo on the Gateway with `--thinking`:

```
- `thinking.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings and trailing spaces. template: `AGENTS.md` found in 1 of 1 runs.
- `thinking.json` on kilo, thinking: set `kilo run --variant high --thinking` (1 run); sent `"thinking": {"type": "disabled"}` (1 run); text none in the events (1 run).
```

Run 4, Claude Code with `--thinking` (the same AGENTS.md line as run 2):

```
- `thinking.json` on claude-code, thinking: set `--settings {"showThinkingSummaries": true}` (1 run); sent `"thinking": {"budget_tokens": 31999, "type": "enabled"}` (1 run); text in the events (1 run).
```

Run 5, Kilo on the Anthropic provider-key route with `--thinking`:

```
- `thinking.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings and trailing spaces. template: `AGENTS.md` found in 1 of 1 runs.
- `thinking.json` on kilo, thinking: set `kilo run --variant high --thinking` (1 run); sent `"thinking": {"type": "enabled", "budget_tokens": 16000, "block_binding": {"prefix_mismatch_behavior": "drop_block"}}` (1 run); text in the events (1 run).
```

Run 6, Claude Code in Docker with both flags (the same AGENTS.md line as run 2, for the same reason):

```
- `thinking.json` on claude-code, thinking: asked `--settings {"showThinkingSummaries": true}; Inspect's reasoning_tokens 31999` (1 run); applied `"thinking": {"type": "enabled", "budget_tokens": 31999, "display": "summarized"}` (1 run); text in the events (1 run).
```

## Re-matched after the review fixes

After the pre-PR review fixes (2026-11-12), `whitney report` on these logs can't print the new lines: each score was written by the code of the day, in the old shape, and the report counts a score in that shape as not captured (`whitney report <logs>/cc-capture` and `<logs>/kilo-capture` both exited 0 and printed "`capture-rules.json` on claude-code: not captured." and the same for kilo). So the new matching was checked offline instead, not by a new run: a scratch script fed each log's stored `sent.first_body`, the run's first model call as it was kept, into the fixed `whitney.capture.Capture`, with the text of `template/<target>/AGENTS.md` as `texts_to_find` gives it (on `claude-code`, with the comment removed) and the model the body names, and rendered the section with `whitney.capture_report`. It printed:

```
- `capture-rules.json` on claude-code: `AGENTS.md` stands for the instruction files staged and the files they import, each looked for as its exact text after removing block-level HTML comments, as Claude Code does, and normalising line endings and trailing spaces. Only what the first model call held counts as sent as an instruction.
  - baseline: `AGENTS.md` not in the first model call in 1 of 1 runs; 1 other request wasn't the run's model call and wasn't searched.
  - template: `AGENTS.md` in the first model call in 1 of 1 runs (messages, under `Contents of …/AGENTS.md (project instructions, checked into the codebase):`); 1 other request wasn't the run's model call and wasn't searched.
- `capture-rules.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings and trailing spaces. Only what the first model call held counts as sent as an instruction.
  - baseline: `AGENTS.md` not in the first model call in 1 of 1 runs; 1 other request wasn't the run's model call and wasn't searched.
  - template: `AGENTS.md` in the first model call in 1 of 1 runs (system prompt, under `Instructions from: …/AGENTS.md`); 1 other request wasn't the run's model call and wasn't searched.
```

This shows the fixed matcher gives the right answer on the requests the real harnesses sent, on both targets. It is not a run: the model-call check was given the model each body named, the later requests' bodies weren't kept, so "only in a later request" couldn't be tried, and the scores in the logs still say what the code of the day said. A new one-epoch run per target (about $0.08, as runs 1 and 2) would show the lines from `whitney run` itself.

### Run 2 again on the fixed code (2026-11-12, `claude-code`)

Command 2 from the table was run again on the branch's final code (two agent runs, Haiku, exit 0). The report `whitney run` wrote says:

```
## What was sent to the model

- `capture-rules.json` on claude-code: `AGENTS.md` stands for the instruction files staged and the files they import, each looked for as its exact text after removing block-level HTML comments, as Claude Code does, and normalising line endings and trailing spaces. Only what the first model call held counts as sent as an instruction.
  - baseline: `AGENTS.md` not in the first model call in 1 of 1 runs; 1 other request wasn't the run's model call and wasn't searched.
  - template: `AGENTS.md` in the first model call in 1 of 1 runs (messages, under `Contents of …/AGENTS.md (project instructions, checked into the codebase):`); 1 other request wasn't the run's model call and wasn't searched.
```

So on `claude-code` the template's rules are found and the baseline's aren't, from a real run. The `kilo` lines above this section are still the offline re-match: run 1 has not been made again on the fixed code.

## What each log holds

Each run's record is under `kilo_run` in the sample's store. Excerpts are from `sent.first_body` and the run's `events`. Temporary paths are shown as `<tmp>`.

**Requests.** Each line gives method, path, bytes and whether it is a model request:

- Kilo on the Gateway (runs 1 and 3): `GET /api/openrouter/models` 0, no; `POST /api/openrouter/messages` about 67,000, yes. The model list loads and the chat call goes in Anthropic's format, as without a proxy, so the origin-alone address of decision 6 holds.
- Kilo with a provider key (run 5): `POST /messages` 65,510, yes, forwarded to `https://api.anthropic.com/v1/messages`.
- Claude Code (runs 2 and 4): `HEAD /api/hello` 0, no; `POST /v1/messages?beta=true` about 87,000, yes.
- Docker (run 6): one request from Inspect's log, 86,839 bytes, with no method or path (Inspect logs neither) and `unlogged` 0.

**The prompt.** Kilo: `"messages": [{"role": "user", "content": [{"type": "text", "text": "Reply with the single word PONG. D…`. Claude Code: the prompt is the last text block of the first user message, after the `<system-reminder>` blocks.

**The `source` text.**

- Kilo template, in `system`:

  ```
  Instructions from: <tmp>/project/AGENTS.md\n# Agent rules\n\nCore rules for every Kilo session. … \n\n## Core rules\n\n- Never commit .env files or secrets\n\n## Team rules\n\n<!-- Add your team's rules here. -->\n
  ```

- Claude Code template, in the first user message:

  ```
  Contents of <tmp>/project/AGENTS.md (project instructions, checked into the codebase):\n\n# Agent rules\n\nCore rules for every Claude Code session. … \n\n## Core rules\n\n- Never commit .env files or secrets\n\n## Team rules\n</system-reminder>
  ```

- Both baselines: no `Never commit` anywhere in the request.

**Thinking excerpts, from `events`.**

- Run 4, Claude Code, a `thinking` block: "The user is asking me to do a simple math calculation: 17 + 25. Let me think through this: 17 + 25 = 42 …"
- Run 5, Kilo with a provider key, a `reasoning` event: "The user is asking me to calculate 17 + 25 and think it through, then reply with the number only. … 17 + 25 = 42"
- Run 6, Claude Code in Docker, a `thinking` block: "The user is asking me to calculate 17 + 25. … Let me calculate: 17 + 25 = 42"
- Run 3, Kilo on the Gateway, has no reasoning event, and `step_finish` has `tokens.reasoning` 0.

## Kilo's `--variant high` with the origin-alone address (open decision 19)

The Task 31 spike saw `--variant high` send `"reasoning": {"effort": "high"}` on the Gateway route. That was in the OpenAI format caused by the wrong `/api/gateway` base URL. With the proxy's origin alone (run 3), Kilo 7.8.27 sends Anthropic's format and **`--variant high --thinking` doesn't reach the model on the Gateway route**: the request has `"thinking": {"type": "disabled"}`, the same as without the flags (run 1), and the model doesn't think. On the provider-key route (run 5) the same flags send `"thinking": {"type": "enabled", "budget_tokens": 16000, …}`, and the thinking text is in the stream. `docs/spike-findings.md` is corrected to match.

So on the Gateway route, `--thinking` is told to Kilo but not applied. The report says so: "set `kilo run --variant high --thinking`" next to "sent `"thinking": {"type": "disabled"}`". It never states a level the request didn't carry.

## Checks on what was kept

- Every log, report and scratch file from these runs was searched for `sk-ant-` and `Bearer `. None held either.
- No log held the home folder's path or the user name. The temporary project path is in each request.
- In Docker, the logged request has an `extra_headers` field holding `accept-encoding`, `anthropic-beta` and `x-irid`, and no credential.
- Claude Code's request carries `metadata.user_id`, a JSON string with a device id and a session id. It is kept as sent; the docs say so.

## What this doesn't show

- One epoch, one scenario, Haiku only, one version of each harness.
- No skill or agent `source` was run, so the separate description and body counts were tested only with hand-built logs.
- The Kilo Docker route wasn't run. It reads Inspect's log the way the Claude Code Docker run did, and it is tested with a fake transcript only.
- Whether `--variant` works on the Gateway route for other models, or through some other setting.
