# Capturing what was sent to the model: the Task 31 spike, 2026-11-12

Task 31 in [tasks/todo-phase5.md](../../tasks/todo-phase5.md) asks for a run's log to hold what the model was sent and the agent's reasoning. This page records the spike that chooses how. The evidence is in [spike-findings.md](../spike-findings.md#phase-5-task-31-what-was-sent-to-the-model-2026-11-12); this page is the runs and their numbers.

## Setup

- **Script:** [`spikes/prompt_capture.py`](../../spikes/prompt_capture.py). Each case stages a git project and runs the harness through Whitney's own runner (`whitney.agent._run_local_process` or `whitney.claude_code._run`), so the temporary HOME, environment allow-list, git isolation and redaction are an eval's. The Docker case runs Whitney's `claude_code` agent itself, with `execution="docker"`.
- **Harnesses:** Kilo 7.8.27 and Claude Code 2.1.322, both on the host; Claude Code 2.1.322 in `claude-code-evals-sandbox:2.1.322-node.1` for the Docker case.
- **Models:** `kilo/anthropic/claude-haiku-4.5` (Kilo Gateway, through the user's Kilo login), `anthropic/claude-haiku-4-5` (Kilo's provider key route, with `ANTHROPIC_API_KEY`), `claude-haiku-4-5` (Claude Code).
- **Project:** the spikes' till (`README.md`, `app.py`, two commits), plus an `AGENTS.md` with a marker line (`CAPTURE-RULE-7319`), a `release-notes` skill whose body has its own marker (`CAPTURE-SKILL-BODY-2287`), and one permission rule: `kilo.jsonc` with `external_directory: ask` and `bash: {"git push*": "deny"}`, or `.claude/settings.json` with `deny: ["Bash(git push:*)"]`.
- **Prompts:** "Reply with the single word PONG and nothing else.", or for the thinking cases "What is 17 + 25? Think it through briefly, then reply with the number only."
- **Raw output** stayed in the session's scratch folder (unredacted plugin captures among it); it is not committed. Committed: three request fixtures in [`tests/data/prompt-capture/`](../../tests/data/prompt-capture/), each with a `.meta.json`.

## The runs

Nine runs called a model, each once; the two `kilo debug config` cases called none.

| Case | Target, route | Capture | Exit | Cost reported by the harness |
|---|---|---|---|---|
| `kilo-plugin-sources` | Kilo, no model | marker plugins in HOME, overlay and project; `kilo debug config` with and without `--pure` | 0, 0 | none |
| `kilo-plugin-overlay-only` | Kilo, no model | a marker plugin in the overlay only | 0, 0 | none |
| `kilo-gateway` | Kilo, Gateway, no `--pure` | plugin and proxy | 0 | $0.0219 |
| `kilo-key` | Kilo, provider key, no `--pure` | plugin and proxy | 0 | $0.0213 |
| `kilo-thinking` | Kilo, Gateway, no `--pure`, `--variant high --thinking` | plugin and proxy | 0 | $0.0227 |
| `kilo-variant-only` | Kilo, Gateway, **`--pure` kept**, `--variant high` | proxy only | 0 | $0.0227 |
| `cc-local` | Claude Code, local | proxy and `OTEL_LOG_RAW_API_BODIES=file:` | 0 | $0.0279 |
| `cc-thinking` | Claude Code, local, `alwaysThinkingEnabled`, `showThinkingSummaries`, `MAX_THINKING_TOKENS=2048` | proxy and OTEL | 0 | $0.0141 |
| `cc-summaries-only` | Claude Code, local, `showThinkingSummaries` only | OTEL only | 0 | $0.0115 |
| `cc-claude-md-import` | Claude Code, local, `CLAUDE.md` is `@AGENTS.md` | OTEL only | 0 | $0.0113 |
| `cc-docker` | Claude Code, Docker, Inspect's bridge | Inspect's log | 0 | $0.0267 (Claude Code's own estimate) |

Total: nine model runs, about $0.18.

## What each capture held

| Case | Requests | First model request | System prompt | Tools | `AGENTS.md` text | Skill description / body | Permission rule text |
|---|---|---|---|---|---|---|---|
| `kilo-gateway` (proxy) | 2 `GET .../models` (405), 1 `POST /api/openrouter/chat/completions` | 67.5 KB, OpenAI format | 13,989 chars, `messages[0]` | 24 (with `websearch`) | exact, `messages[0]` | yes / no | no |
| `kilo-gateway` (plugin) | hooks `messages`, `system`, `params` once each | | 13,967 chars, same text | none | exact | yes / no | no |
| `kilo-key` (proxy) | 1 `POST /v1/messages` | 65.5 KB, Anthropic format | 13,967 chars, `system[0]` | 23 | exact, `system[0]` | yes / no | no |
| `cc-local` (proxy) | 1 `POST /v1/messages?beta=true` | 87.1 KB | 28,219 chars | 12 | exact, `messages[0].content[5]` | yes / no | no |
| `cc-local` (OTEL file) | 1 request, 1 response, `index.jsonl` | 87.4 KB, the same body plus `betas` | the same | 12 | exact | yes / no | no |
| `cc-claude-md-import` (OTEL) | 1 | 88.8 KB | 28,824 chars | 12 | exact, after its own "Contents of …/AGENTS.md" header | yes / no | no |
| `cc-docker` (Inspect log) | 1 model event | `call.request` 86.6 KB, with attachments resolved | in `system` | 12 | exact | yes / no | no |

"Exact" means the file's whole text was a substring of the request's text. Normalising line endings and trailing spaces changed no result.

## Thinking

| Case | Sent | In the harness's stream | In the response |
|---|---|---|---|
| Kilo, no variant (`kilo-gateway`, `kilo-key`) | no reasoning parameter | nothing | nothing |
| `kilo-variant-only` | `"reasoning": {"effort": "high"}` | no `reasoning` event; `step_finish` counts 137 reasoning tokens | the reasoning text, in the proxy's record |
| `kilo-thinking` | the same | two `reasoning` events with the text in `part.text`; 109 reasoning tokens | the same |
| Claude Code, no setting (`cc-local`, `cc-claude-md-import`) | `"thinking": {"type": "enabled", "budget_tokens": 31999, "display": "updates"}` | a `thinking` block whose `thinking` is `""`, with a `signature` | `""` (proxy); `"<REDACTED>"` (OTEL); 53 thinking tokens |
| `cc-summaries-only` | `{"type": "enabled", "budget_tokens": 31999}` (no `display`) | the thinking text | the text (OTEL still `"<REDACTED>"`) |
| `cc-thinking` | `{"type": "enabled", "budget_tokens": 2048}` | the thinking text | the text (proxy) |
| `cc-docker` | no `thinking` in Inspect's `call.request` | none | none |

## Secrets

- Header values were never recorded; the proxy keeps header names only. The key travelled in `x-api-key` (Anthropic) or `authorization` (Gateway).
- No key or login value was in any proxy record, OTEL body, transcript or Inspect log.
- **The Kilo plugin's `chat.params` hook was handed the credentials:** its captures held the Kilo login's values twice (Gateway runs) and `ANTHROPIC_API_KEY` twice (key route), under the hook's `provider` input.
- A fake key put in a captured request's tool result survives `redact_run` when the request is kept in a field of its own, and is replaced when the request is passed through `redact` as text (`secrets-demo` in the analysis).

## Not shown

- One run per case, Haiku only, one prompt each, and runs of one model call. Nothing here shows whether a harness's system prompt changes between the steps of a longer run.
- Kilo in Docker. Its model calls go through the same bridge (T1, 2026-11-02), so Inspect's log should hold them as it does Claude Code's; not checked here.
- Whether Kilo's `--thinking` reasoning events appear when a step also calls tools.
- Which of `alwaysThinkingEnabled` and `showThinkingSummaries` matters when both are set: `cc-summaries-only` shows the second alone is enough.
- Whether a very long `AGENTS.md` or `CLAUDE.md` is cut before it is sent.
- Claude Code's OTEL route in Docker, and the proxy's behaviour under a retry or a request that fails upstream.
