# omp spike, first cut: the 2 stop conditions

Run pinned omp a few times through a spike script built like `spikes/claude_code_shapes.py`, reusing its sanitiser. It answers only Task 58's 2 stop-and-report questions before any more of the 2-day box is spent: does `omp -p --mode json` record tool inputs, and does a temporary HOME keep `~/.omp` and `~/.claude` out?

## Job story

When I'm deciding whether omp is worth the rest of a 2-day, 2-dollar spike as Whitney's third target
I want to know early whether its headless stream and its isolation meet the 2 conditions Whitney can't work without
So I can stop the spike for a few cents if either fails, instead of finding out after mapping every check

## Acceptance criteria

- [ ] Given Christopher's go-ahead for the spend, recorded and dated under Task 58, and omp installed at a pinned version in a throwaway container that mounts only the staged project and an output folder, from a saved copy of its install script that was read before that same copy was run, when I run the spike's edit case on a staged copy of Whitney's template project under an empty temporary HOME, then a sanitised `tests/data/omp-events/edit-file.jsonl` and `edit-file.meta.json` exist, the meta file names the omp version, model, prompt and date the way the Claude Code fixtures' meta files do, and the file on disk changed. The findings name the event and field that hold that call's arguments (the path and the new text), or say they're absent. A run with no tool call is re-run with a more direct prompt and doesn't count as "inputs missing". Inputs are reported missing only after the modes docs were checked for a setting that adds them.
- [ ] Given a temporary HOME with a planted `~/.omp/agent/AGENTS.md`, `~/.claude/CLAUDE.md` and a `.env` in HOME and in the project's parent folder, each carrying its own canary and a stated way to see it in the stream (for an instruction file, a word the run is asked to repeat; for a `.env`, a variable the run is asked to print), when I run the spike's control case without isolation and then its isolation case with whichever of `--profile` and `disabledProviders` it needs, then sanitised `isolation-control.jsonl` and `isolation.jsonl` fixtures exist. The findings list each planted file as seen in the control and seen or not in the isolated run. A canary the control never showed is reported as "not observable", not as "kept out". Every canary is a made-up word that isn't shaped like a key, each planted file is written by the spike script, and none is a copy of a real one.
- [ ] Given the same 2 instruction files planted in the container account's own home folder (the one its user record names) and HOME set to an empty temporary folder, as `src/whitney/claude_code.py` sets it, when I run the spike's empty-HOME case, then a sanitised `isolation-empty-home.jsonl` exists and the findings say whether either canary showed. This is the run that answers "does a temporary HOME keep them out", so if the control in the criterion above never showed these canaries, the answer is reported as "not shown".
- [ ] Given both cases have run, when I open `docs/spike-findings.md`, then it has an omp section with the version, every docs URL read with its date, each finding marked "shown by a run" or "from the docs", and a verdict. The verdict is either stop, naming the condition that failed, or carry on, naming the next run. Stop means 1 of 2 things: the edit call's path and new text aren't in the stream, or a `~/.omp` or `~/.claude` canary showed under every isolation tried. A `.env` canary that got through is reported but isn't a stop on its own, since Task 58's rule names only those 2 folders. A condition no run could answer is named as unanswered, not as passed.
- [ ] Given the runs are done, when the fixtures are checked before committing, then gitleaks finds nothing in the fixtures, their meta files or the omp section, and no local path, `sk-` key, user name or email address is in them. The omp section gives the container's run command, which shows no mount of the host's home folder and `ANTHROPIC_API_KEY` passed in the environment only, never written to a file or an image layer. `python scripts/check.py task` passes, and `git diff main --stat` shows no change under `src/`.

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `spikes/claude_code_shapes.py` — the shape to copy: cases, `--out` for raw output outside the repo, `--fixtures` to write sanitised ones
- `spikes/skill_agent_shapes.py` — `sanitise`, `sanitise_emails` and `_trim`, the fixture sanitising every spike shares
- `spikes/run_to_fixture.py` — the refusal to write a fixture that still holds a path, key, user name or email
- `src/whitney/claude_code.py` — `_run`'s temporary HOME and environment allow-list, the isolation an omp run should match
- `tests/data/claude-code-events/agent-denied-builtin.meta.json` — the meta fields a fixture carries
- `docs/spike-findings.md` — the "Phase 5 Task 36a" section is the shape for the omp section (what was set, what happened, recommendation, not shown)

## Owed

- Task 58's box stays unticked even on a "carry on" verdict: the check-mapping table, the recommendation and 3 of its 4 capture runs (deny, pre hook, skill) remain, as the next slice. It stays unticked on a "stop" verdict too. Closing Task 58 without its table and recommendation is Christopher's call.
- No prompt capture through Task 31's proxy. An instruction-file canary is seen only through the model's reply, so "kept out" rests on the control showing the same canary first, and on 1 run each. A model that ignored the canary in the isolated run would read as "kept out". The proxy capture in the next slice is what settles it.
- Telemetry isn't checked, so these runs happen without knowing what omp sends beyond the model call. The container holds only the staged template and the planted canaries, which limits what could be sent. The key is the exception.
- Isolation is shown for `~/.omp` and `~/.claude` only. `~/.codex` and the other harness folders Task 58 names aren't planted.
- Nothing checks the host's real `~/.omp`, `~/.claude` or `~/.env`. The container not mounting them is the guard. A checksum of the real `~/.claude` was considered and dropped: Claude Code sessions write there all the time, so it would change with omp doing nothing, and it catches a write but not a read.

## Assumptions

- Spend go-ahead — needed before the first run, and it's a precondition in criterion 1. The guess is that Christopher approves the few cents these 5 or so runs cost, and the rest of Task 58's 2 dollars waits on the verdict. The guess for this slice's own box is half a day and 25 cents. Past either, stop and report what's known.
- Model route — Haiku through `ANTHROPIC_API_KEY`, as the Claude Code spikes used. Inspect's bridge isn't tried.
- Pinned version — the current omp release on the day the spike runs, recorded in the meta files.
- Where it runs — a throwaway container, so the install script can't touch the host, and so omp's default `yolo` approval can't run a shell command as Christopher. Network is limited to the model API once omp is installed, if that's cheap to do. Task 58 also allows a throwaway local folder. This slice doesn't use it: on the host, a failed isolation, the thing being tested, would send his real `~/.claude` to the model and into the raw output. If the container route costs more than half a day, stop and ask him.
- Install — the install script is saved to a file, read, and that same file is run, not fetched a second time. Its SHA-256 goes in the findings. If the docs offer a package install that takes a version number, that's used instead.
- Fixture folder — `tests/data/omp-events/`, next to `kilo-events/` and `claude-code-events/`.
- Fixtures are committed — as the other spikes' fixtures are, even if the verdict is stop, so the finding can be checked again.
- `AGENTS.md` — not edited. Naming omp there is Christopher's call, and it waits for the full spike's recommendation.
