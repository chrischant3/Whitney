# Reading a file before the first edit, tested end to end, 2026-11-13

Does the agent read `CONSTRAINTS.md` before it edits code when a rule says so? Task 51 of the Later plan ([tasks/plan-later.md](../../tasks/plan-later.md)) added the `read_before_edit` check, gave it an example project in [`examples/read-before-edit/`](../../examples/read-before-edit/), and ran it for real three times. The rule's wording changed between runs, because the template didn't hold in the first two.

- **The eval file**, `evals/agents-md.json`: two prompts that ask for a change to `till.py`, each expecting `read_before_edit` with `file` set to `(^|/)CONSTRAINTS\.md$`. The fixture's project holds a short `CONSTRAINTS.md`. The second prompt, `shell-only`, asks the agent to look at files only with shell commands, so a run reads through `cat`.
- **The baseline**, the default for an `AGENTS.md` source: the same prompts with `AGENTS.md` removed. Without the rule, nothing points the agent at `CONSTRAINTS.md`.

Every run used Kilo 7.8.27 and `kilo/anthropic/claude-haiku-4.5`, in local execution. The logs were kept in a scratch folder and are not committed. Three runs' streams are saved, sanitised, in `tests/data/kilo-events/`: `read-then-edit`, `edit-without-read` and `shell-read-then-edit`.

```bash
whitney validate --target kilo --project-path examples/read-before-edit --evals-path examples/read-before-edit/evals
whitney validate --target claude-code --project-path examples/read-before-edit --evals-path examples/read-before-edit/evals
whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
  --project-path examples/read-before-edit --evals-path examples/read-before-edit/evals \
  --epochs 1                 # run 1; runs 2 and 3 added --limit 1
```

Both `validate` commands printed `examples/read-before-edit/evals: 1 eval file(s), 0 problem(s), 0 warning(s)` and exited 0, with each wording of the rule.

## The three runs

| Run | The rule in `AGENTS.md` | Scenarios | baseline | template | `whitney run` exit |
|---|---|---|---|---|---|
| 1 | "Read `CONSTRAINTS.md` before you edit or create any file." | both | 0/2 | 0/2 | 1 |
| 2 | "Before your first edit, read `CONSTRAINTS.md` in the project root. It holds rules every change must follow, so read it even when the task looks small." | `add-a-function` | 0/1 | 0/1 | 1 |
| 3 | "Your first action in every task is to read `CONSTRAINTS.md`, before you read or edit any other file. It holds rules every change must follow." | `add-a-function` | 0/1 | 1/1 | 0 |

Runs 1 and 2 exited 1 because the template's rate, 0%, was below the check's 80% gate (the report's Problems: "agents_md: template 0% is below the minimum 80%"). Run 3 exited 0. The example now holds run 3's wording.

The report's line for the check, from each run's score explanation (the temporary project folder shortened to `…/project`):

- **Run 3, template, read first.** "read of `…/project/CONSTRAINTS.md` (call 1) came before edit of `…/project/till.py` (call 4)". The agent read `CONSTRAINTS.md`, listed the files with `glob`, read `till.py` and edited it. Saved as `read-then-edit`.
- **Every baseline run, edited unread**, such as run 1's `add-a-function`: "edit of `…/project/till.py` (call 2) ran with no read of a file matching file before it". The agent read `till.py` and edited it. Saved as `edit-without-read`.
- **Runs 1 and 2, template, edited unread.** The same line as the baseline's: with the rule in its first two wordings, the agent still read only `till.py` (and in `shell-only`, `cat till.py`) before editing. Kilo staged `AGENTS.md` in each run (the report's "Staged files"). The rule was there and not followed. This is a finding about Haiku and the rule's wording, not about the check: a "read X before editing" rule needed "your first action", not "before you edit", before the agent followed it once.
- **Run 1, baseline, `shell-only`**: `cat till.py` (exit 0), an edit of `till.py`, then `cat till.py` again. It is the real shell read: judged with `file` set to `till\.py$` it passes on the `cat` (`tests/test_checks_reads.py`). Saved as `shell-read-then-edit`.

The explanation shows a long path by its end, and on macOS the temporary folder is long, so most of the 80 characters shown are the folder. The file's name is still there.

## What it cost

Kilo's own figures in the eight streams: $0.116 for run 1 (four runs), $0.056 for run 2 and $0.059 for run 3, $0.231 in all.

## What this doesn't show

- **One epoch, one model, one harness version.** The template held in one run out of four, and only with the third wording. Treat the result as showing the check can pass and fail on real output, not as a rate. The `shell-only` scenario hasn't run with the third wording.
- **Claude Code.** No real run on `claude-code` was made (no API key on this machine). The check is tested there on Claude Code 2.1.322's saved streams: `skill-tool-denied` and `skill-denied` (`Read`s, then an `Edit`) and `outside-bash-read` (a shell read with `cat`, no edit). No saved Claude Code stream holds an edit with no read before it, nor a `Write` or `NotebookEdit` call.
- **A shell read of the rule file in a real run.** The real shell read is of `till.py`. A `cat CONSTRAINTS.md` is tested on built streams only.
- **What the rule says beyond the check.** The rule asks for `CONSTRAINTS.md` before any other file; the check tests only that it came before the first edit, so an agent that read `till.py` first would still pass.
- **Changes after review.** After these runs, review made the check stricter about shell reads (output piped into `grep` or redirected isn't a read; a read whose exit may be another command's is unsure) and about a read made in the same step as the edit. The saved streams were re-scored offline and keep their verdicts (`tests/test_checks_reads.py`): none of their reads shares the edit's step or uses a pipe. So no new paid run was made.
- **A partial read, a shell write before the read, or a subagent on Kilo**, in a real run. None of the eight runs had one. Each is tested on streams built from the real events.
