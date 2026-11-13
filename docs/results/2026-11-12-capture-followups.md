# Task 31 follow-ups: the Gateway proxy's base URL and `CLAUDE.md` imports, 2026-11-12

Christopher answered Task 31's seven decisions on 2026-11-12 ([tasks/todo-phase5.md](../../tasks/todo-phase5.md#task-31-record-what-was-sent-to-the-model-and-the-agents-reasoning)). Two of them asked for evidence first: decision 6 (which base URL the logging proxy should have on the Kilo Gateway route) and decision 7 (whether a Whitney run on `claude-code` loses what a `CLAUDE.md` imports). This page is those runs. The findings are in [spike-findings.md](../spike-findings.md#phase-5-task-31-follow-up-gateway-base-url-and-claudemd-imports-2026-11-12).

## Setup

- **Script:** [`spikes/capture_followups.py`](../../spikes/capture_followups.py), using the logging proxy of [`spikes/prompt_capture.py`](../../spikes/prompt_capture.py). The proxy records each request's path, its header names and its body, never a header's value. Kilo's plugin route was not used.
- **Harnesses:** Kilo 7.8.27 and Claude Code 2.1.322, on the host, through Whitney's own runners (`whitney.agent._run_local_process`, `whitney.claude_code._run`): temporary HOME, environment allow-list, git isolation, `--pure` on Kilo, `--setting-sources project` on Claude Code.
- **Models:** `kilo/anthropic/claude-haiku-4.5` (Gateway, the user's Kilo login) and `claude-haiku-4-5` (Claude Code, `ANTHROPIC_API_KEY`). Prompt: "Reply with the single word PONG and nothing else."
- **Raw output** stayed in the session's scratch folder and is not committed. Committed: one request fixture, `tests/data/prompt-capture/kilo-gateway-origin.request.json`, with its `.meta.json` (gitleaks: no leaks).

## Decision 6: the Gateway base URL

Three forms of the `kilo` provider's `baseURL`, each the proxy's address plus a path. The proxy forwarded every path unchanged to `https://api.kilo.ai`. `gw-models` ran `kilo models kilo --pure` once per form (no model call); each `gw-run-*` ran one `kilo run --pure`.

| Form | `baseURL` path | `kilo models kilo` | Model-list calls in the run | Chat call | Request format | Exit, cost |
|---|---|---|---|---|---|---|
| `path` (today's `credentials.DEFAULT_GATEWAY_URL` path) | `/api/gateway` | 368 models listed; 2 × `GET /api/gateway/api/openrouter/models` → **405** | 2 × `GET /api/gateway/api/openrouter/models` → 405 | `POST /api/openrouter/chat/completions` → 200 | **OpenAI** chat completions: system prompt as `messages[0]`, no `thinking` key | 0, $0.0219 |
| `origin` | none | 321 models listed; 1 × `GET /api/openrouter/models` → 200 | 1 × `GET /api/openrouter/models` → 200 | `POST /api/openrouter/messages` → 200 | **Anthropic** messages: `system`, `"thinking": {"type": "disabled"}` | 0, $0.0219 |
| `openrouter` | `/api/openrouter` | 321 models listed; 1 × `GET /api/openrouter/models` → 200 | 1 × `GET /api/openrouter/models` → 200 | `POST /api/openrouter/messages` → 200 | Anthropic messages, the same as `origin` | 0, $0.0219 |

All three requests held 24 tools and were about 67.5 KB; only the format and the path differed. With `/api/gateway`, Kilo appended `/api/openrouter/models` to the whole base URL for the model list, but built the chat path from the origin, and when the list failed it fell back to another list (368 entries) and to the OpenAI format.

## Decision 7: `CLAUDE.md` imports

Each project was run as itself ("real": Claude Code in the project folder, every file there) and as Whitney stages it ("whitney": `load_claude_code_project_files`, then `staged_project`, which is what `whitney run --project-path` does). Both went through `claude_code._run`. "Sent" means the marker was in the first request's text; Claude Code puts each file it read under its own `Contents of <path>` header.

**P1**, every import form at once. `CLAUDE.md` has its own text and imports `@AGENTS.md`, `@docs/rules.md` (a chain of six files, each importing the next, relative to its own folder), `` `@docs/span.md` `` in a code span, `@docs/fenced.md` in a fenced block, `@~/home-rule.md` (written into the temporary HOME), an absolute path outside the project, `@../outside/rel.md`, an absolute path inside it, `@config/.env.shared` and `@docs/link.md` (a symlink to a file in `docs/`). `AGENTS.md` imports `@docs/agents-extra.md`. `.claude/CLAUDE.md` has its own text and imports `@../docs/from-dot.md`. `.claude/rules/style.md` exists.

**P2:** `CLAUDE.md` is `@AGENTS.md` and nothing else. **P3:** `AGENTS.md` alone, importing `@docs/agents-only.md`.

| Case | Real project sent | Whitney run sent (before the fix) | Differs? |
|---|---|---|---|
| P2: `CLAUDE.md` is only `@AGENTS.md` | `AGENTS.md`'s text (`cc-claude-md-import` in the earlier spike, and P1 here) | nothing: `CLAUDE.md`'s one line `@AGENTS.md`, with no file to import (`imp-p2-whitney`) | **yes: the whole rule set missing** |
| P1: `CLAUDE.md`'s own text plus `@AGENTS.md` | own text and `AGENTS.md`, and `AGENTS.md`'s own import | own text only | **yes** |
| P1: `@docs/rules.md` and its nested imports | hops 1 to 4 (`rules.md`, `two.md`, `three.md`, `four.md`); not hops 5 and 6 | none | **yes** |
| P1: `.claude/CLAUDE.md` importing `@../docs/from-dot.md` | sent: resolved from `.claude/` | not sent | **yes** |
| P1: absolute path inside the project | sent | not sent | **yes** |
| P1: `@docs/link.md`, a symlink inside the project | sent | not sent | **yes** |
| P1: in a code span, in a fenced block | not sent | not sent | no |
| P1: `@~/home-rule.md`, absolute outside, `@../outside/rel.md` | not sent: external imports need a person's approval, which `-p` in a fresh HOME never has | not sent | no |
| P1: `@config/.env.shared` | not sent (why isn't shown) | not sent | no |
| P1: `.claude/rules/style.md` (not an import) | **sent**, under its own header | not sent: the loader names `.claude/rules/` "rest of .claude/" | **yes, a separate gap** |
| P3: `AGENTS.md` alone importing a file | `AGENTS.md` and the import | `AGENTS.md` only (`imp-p3-whitney`) | **yes** |

**After the fix** (the loader stages imports, below), the same staged runs:

| Case | Staged | Sent |
|---|---|---|
| `imp-p2-whitney-fixed` | `CLAUDE.md`, `AGENTS.md` | `AGENTS.md`'s marker |
| `imp-p3-whitney-fixed` | `AGENTS.md`, `docs/agents-only.md` | both markers |
| P1, loader only (no model) | refused: `…/CLAUDE.md imports @docs/link.md, which Claude Code would read, but Whitney doesn't stage it: it is a symlink, or inside a symlinked folder. Replace the link with the file it points to` | |

With the link replaced, P1 would stage every file the real run sent except `.claude/rules/style.md`, and name the three outside imports and the `.env` import in the report's "Not Staged" table.

## Red, then green

The 21 tests in `tests/test_claude_code_imports.py` were written first. Against the loader before the change, with only the new constants added so they could import: 20 failed, 1 passed (`test_an_unreadable_instruction_file_names_no_imports`, which guards that a file that can't be read names nothing, before and after). For example:

```text
test_a_claude_md_that_only_imports_agents_md_stages_agents_md_too
E       AssertionError: assert {'CLAUDE.md': '@AGENTS.md\n'} == {'CLAUDE.md':...gents rule\n'}
E         Right contains 1 more item:
E         {'AGENTS.md': 'agents rule\n'}
test_imports_are_followed_four_hops_and_no_further
E       AssertionError: assert ['CLAUDE.md'] == ['CLAUDE.md',....md', 'h4.md']
test_imports_outside_the_project_are_named_not_staged
E       AssertionError: assert [] == ['CLAUDE.md: ..../outside.md']
test_an_import_that_is_a_symlink_is_refused_since_claude_code_would_read_it
E       Failed: DID NOT RAISE ValueError
```

After: 21 passed, and the whole suite, 3,081 tests with these, passed with no other test changed. `check.py task` then left touched-function mutants alive (94.9% of 275); five more import tests and one settings test (`tests/test_project_files.py::test_settings_under_the_evals_path_are_named_with_the_rest_of_dot_claude`, for a survivor in `_not_staged`'s unchanged settings line) were added, and the code was reshaped so no equivalent mutant remained (the depth check moved to where an import is queued; the symlink check compares the path with its resolved self; text is decoded with `bytes.decode()`). Final gate: 3,087 tests, changed-line coverage 100%, touched-function mutation score 100% of 266 mutants.

## Through the CLI

`whitney validate --target claude-code --project-path <project> --evals-path <evals>`, with Whitney's own `evals/agents-md.json`:

| Project | Before the fix | After |
|---|---|---|
| P2 (`CLAUDE.md` is `@AGENTS.md`) | exit 0: `AGENTS.md` stands for the staged `CLAUDE.md`, so nothing warned that the rules were missing | exit 0: `1 eval file(s), 0 problem(s), 0 warning(s)`, and `AGENTS.md` is staged |
| P1 (with the symlinked import) | exit 0 | exit 1: `the claude-code loader can't stage this project: …/CLAUDE.md imports @docs/link.md, which Claude Code would read, but Whitney doesn't stage it: it is a symlink, or inside a symlinked folder. Replace the link with the file it points to` |

The "before" column is from the loader's rules, not a CLI run: the before-fix loader staged P2's `CLAUDE.md` and P1's two `CLAUDE.md` files without complaint, as `imp-p1-whitney` and `imp-p2-whitney` show.

## Runs and cost

Ten model runs: three Kilo (`gw-run-path`, `gw-run-origin`, `gw-run-openrouter`, $0.0219 each) and seven Claude Code (`imp-p1-real` $0.0297, `imp-p1-whitney` $0.0142, `imp-p2-whitney` $0.0138, `imp-p3-real` $0.0139, `imp-p3-whitney` $0.0138, `imp-p2-whitney-fixed` $0.0279, `imp-p3-whitney-fixed` $0.0139). About $0.20 in all. `gw-models` called no model.

## Not shown

- One run per case, Haiku only, one prompt, one Kilo and one Claude Code version.
- Why Claude Code didn't send `config/.env.shared`: its name, its leading dot, or something else. The loader names an imported `.env` file as left out either way.
- An import whose path ends in punctuation (`@docs/rules.md.`), an import of a file that doesn't exist or of a folder (the loader passes over both, as nothing is there), and an import of a non-UTF-8 file in real Claude Code (the loader refuses it).
- External imports a person has approved. On the owner's machine, after approval, Claude Code loads them; a Whitney run never does, and names each one.
- The `openrouter` form beyond these two calls, and `KILO_API_URL` as a fourth way: Kilo reads it for its other services too (sign-in, profile), so it would move more than the provider's calls. Not run.
- Kilo's thinking settings with the `origin` form. The earlier spike's `"reasoning": {"effort": "high"}` for `--variant high` was sent in the OpenAI format the `path` form caused; what Kilo sends in the Anthropic format wasn't checked. (Checked later on 2026-11-12: on the Gateway route `--variant high --thinking` still sends `"thinking": {"type": "disabled"}`, so it doesn't reach the model; on a provider key it sends `budget_tokens` 16,000, [runs](2026-11-12-prompt-capture-runs.md#kilos---variant-high-with-the-origin-alone-address-open-decision-19).)
