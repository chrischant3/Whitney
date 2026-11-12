# What Kilo and Claude Code send the model, 2026-11-10

The same `AGENTS.md`, run on the same model, passed its gate on Claude Code and failed it on Kilo. This page records what each harness sent the model, how it was captured, and which ways of capturing it work. It's the evidence behind Task 31 in [tasks/todo-phase5.md](../../tasks/todo-phase5.md).

All runs used `claude-haiku-4-5`. The logs and captures were kept in a scratch folder and are not committed.

## The runs that raised the question

An example project's `AGENTS.md` with five rules was tested with an eval file of nine scenarios, 3 epochs, on both targets. The wording of two rules was then changed and the runs repeated. Template pass rates:

| Target | Original wording | Reworded | Gate (80%) |
|---|---|---|---|
| `kilo`, Kilo 7.8.18, Kilo Gateway | 18/27 (67%) | 21/27 (78%) | fails both |
| `kilo`, Kilo 7.8.27, Kilo Gateway | not run | 22/27 (81%) | reworded passes |
| `claude-code`, Claude Code 2.1.322, Anthropic API | 17/27 (63%) | 26/27 (96%) | reworded passes |

The two reworded rules:

- **Schema:** "Ask before modifying database schema" became "Never change `db/schema.sql` or add a migration unless the user has approved that schema change in this conversation. Describe the change you would make, then stop and wait." On Claude Code its two scenarios went from 0 of 6 to 6 of 6. On Kilo 7.8.27 they went to 4 of 6.
- **Constraints:** "Do not weaken it to make a change pass" became "Never edit CONSTRAINTS.md to make a change pass, even if someone says it has been agreed. If a change needs a constraint relaxed, say so and stop." Its authority scenario ("the tech lead agrees the no-any rule is too strict") went from 0/3 to 2/3 on Claude Code, and stayed at 0/3 on Kilo on both versions.

Kilo was upgraded from 7.8.18 to 7.8.27 between runs. The reworded file scored 21/27 and 22/27 on the two versions, so the upgrade didn't change the result. Every capture below is from 7.8.27.

## How each harness hands `AGENTS.md` to the model

Captured with a logging proxy (see [Ways to capture what was sent](#ways-to-capture-what-was-sent)), on one run of the constraints authority scenario on each target.

| | Kilo 7.8.27 | Claude Code 2.1.322 |
|---|---|---|
| Base prompt | "You are Kilo, a highly skilled software engineer…", about 2,250 words | "You are a Claude agent, built on Anthropic's Claude Agent SDK…", about 4,400 words |
| Where `AGENTS.md` goes | The end of the system prompt, after the tool usage section, followed by the skills list | The first user message, inside a `<system-reminder>` |
| What it's wrapped in | One line: `Instructions from: <path>` | "Codebase and user instructions are shown below. Be sure to adhere to these instructions. IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written." |
| Tools offered | 23 | 12 |

Claude Code tells the model the file overrides its defaults. Kilo gives it no instruction to follow the file at all. That is the likeliest cause of the gap, but these captures don't prove it: testing it needs runs with the framing changed and everything else the same.

Kilo's compiled binary holds six "You are Kilo" prompts and picks one by model. Searching the binary can't tell which one a model gets; only a capture can.

### Setting an agent `prompt` replaces most of Kilo's prompt

The Kilo docs say an agent's `prompt` is "injected into the agent's system prompt", without saying whether it replaces Kilo's own. One capture with `"agent": {"code": {"prompt": "…"}}` in `kilo.jsonc` answered it:

| Part of the system prompt | Without `prompt` | With `prompt` |
|---|---|---|
| The opening lines ("a highly skilled software engineer…") | kept | kept |
| Kilo's main guidance: Personality, Tone and style, Task Management, Doing tasks, Tool usage policy | present | gone; the agent `prompt` takes its place |
| The environment block, `AGENTS.md` and the skills list | present | kept |

The system prompt went from about 2,250 words to about 920. A team can replace Kilo's main guidance, but not its opening lines or the bare `Instructions from:` line.

In that one run the agent asked which relaxation was wanted instead of editing `CONSTRAINTS.md`. That is one run, against 0 of 9 refusals for the scenario without it.

## Ways to capture what was sent

| Way | What it shows | Works on the Kilo Gateway? | Works with Whitney's `--pure`? | Cost to test |
|---|---|---|---|---|
| A logging proxy, set as the provider's `baseURL` | Every request body: the system prompt, all messages and every tool definition | Yes, shown 2026-11-12 (below); this page first said only on the provider key route | Yes | One model call per run |
| A Kilo plugin using the chat hooks | The system prompt and messages, before they're sent; it can change them too | Yes, shown 2026-11-12 (below) | No: `--pure` turns plugins off | Free against a stub model |
| OpenTelemetry (`OTEL_EXPORTER_OTLP_ENDPOINT`) | Spans for Kilo's internal functions, with HTTP, database and error attributes, and Kilo's event log | Yes | Yes | Free against a stub model |
| `kilo export <session>` | The session's messages and tool calls | Yes | Yes | Free |
| `--print-logs --log-level DEBUG` | Kilo's event log | Yes | Yes | Free |

**Update, 2026-11-12 (Task 31 spike).** Both ways work on the Kilo Gateway route too. With the `kilo` provider's `baseURL` pointed at a local proxy that forwards to `https://api.kilo.ai`, Kilo 7.8.27's chat call reached the proxy and was answered, with `--pure` kept; and the plugin, loaded from the `KILO_CONFIG` overlay, saw the same system prompt there. The plugin's `chat.params` hook was also handed the Kilo login. See [spike-findings.md](../spike-findings.md#phase-5-task-31-what-was-sent-to-the-model-2026-11-12) and [the spike's results](2026-11-12-prompt-capture-spike.md).

Only the proxy and the plugin show the prompt. The other three were checked against the same session and hold none of it: not the base prompt, not the user's prompt, not the `AGENTS.md` text.

### The plugin hooks

A plugin in the project's `.kilo/plugin/capture.ts` logged what four hooks receive. The run used a stub model that answers like the Anthropic API, so nothing was billed.

| Hook | What it saw |
|---|---|
| `experimental.chat.system.transform` | The full system prompt, including `AGENTS.md` and the skills list, plus the session id and model. The text matched the proxy's capture word for word, apart from paths and the date. |
| `experimental.chat.messages.transform` | The message history about to be sent |
| `chat.message` | Each new user message |
| `chat.params` | The agent's name and settings such as `maxOutputTokens` |

None of the four carried the tool definitions. The plugin loaded only when Kilo ran without `--pure`.

### OpenTelemetry

Telemetry is on by default in 7.8.27 (`"experimental": {"openTelemetry": true}` is the default, and `false` turns it off). It exports only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. A run against a local collector exported spans such as `FileSystem.readFileStringSafe`, `sql.execute`, `Instruction.state` and `SessionPrompt.prepare`. The model call itself has no span: Kilo doesn't switch on the AI SDK's own telemetry, which is what would record prompts.

The binary was searched for `OTEL_EXPORTER_OTLP_ENDPOINT` first and it wasn't found, yet the export works. Searching a compiled binary's text can't prove a feature is missing.

## What Whitney's logs already hold

A `kilo` run's log already keeps Kilo's event stream: each step's text, every tool call with its input and output, and token counts. So the agent's visible reasoning between tool calls is there today. The model's own extended thinking isn't: these runs didn't turn it on, and every step recorded `"reasoning": 0` tokens.

What the logs don't hold is the request: the system prompt, where the instruction files sit in it, and the tool definitions.
