# Model-graded checks: spike, then a decision

Grade 20 real final reports from the example's `reviewer` agent with Inspect's own `model_graded_qa`, changing only its instructions, against Christopher's marks and 5 broken controls. That lets him decide, on a 90% bar set before the spike, whether Whitney gets a model-graded check. A new scorer is out of scope, and a follow-on task is drafted only if he says yes.

## Job story

When I have an eval file for a skill or agent whose output is a report, and every check Whitney has only proves what it mustn't do
I want to know whether a model grader agrees with my own marks on those reports often enough, and cheaply enough, to trust it
So I can decide with evidence whether to build a labelled model-graded check that stays out of the gate, instead of guessing and shipping a grade nobody has measured

## Acceptance criteria

- [ ] Given 20 final reports from the example project's `reviewer` agent on the `till` fixture on the `claude-code` target, from new runs or from kept logs named in the write-up, when they're collected, then they're saved under `docs/results/`, each with an id. Next to them are 5 controls and a marks file with one empty mark per report for Christopher. Each control is a copy of a report that holds at least one file-and-line reference, with every file name and line number removed, so it differs from its source.
- [ ] Given the marks file with any mark still empty, when the spike's grade step runs, then it stops before any grader call and names the ids that are still unmarked.
- [ ] Given all 20 marks filled in, when the grade step runs, then a results file under `docs/results/` shows Christopher's mark for each report next to the grader's grade and explanation. It also gives the agreement rate over the 20, lists every disagreement by id, and shows the grader's grade on each of the 5 controls.
- [ ] Given any of the 5 controls graded as a pass, when the results are written, then the results file and `docs/spike-findings.md` say the grader isn't usable, whatever its agreement rate.
- [ ] Given the spike has run, when I open `docs/spike-findings.md`, then a dated section gives the grader model, the full grading prompt, the cost per report from the log's token counts and the agreement rate against the 90% bar. It also records the exact collect and grade commands, Christopher's approval of any new collection runs before they ran, and a link to the results file.
- [ ] Given Christopher's decision, when it's recorded, then `docs/spike-findings.md` and Task 54 in `tasks/plan-later.md` carry it with its date. If the decision is yes, `tasks/plan-later.md` also gets a drafted task whose criteria keep the grade out of the gate by default and label it as model-graded in the report.
- [ ] Given the spike is done, when `python scripts/check.py task` runs, then it passes and no file under `src/whitney/` has changed.

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `examples/consumer/.claude/agents/reviewer.md`: the report under test. Its line "each problem with its file, its line and how to fix it" is the rubric that both the grader and Christopher mark against.
- `examples/consumer/evals/agents/claude-reviewer.json`: has prompts that already get the reviewer to report on `till.py`. Reuse its positive trigger prompts to collect replies.
- `src/whitney/claude_code.py`: `_outcome` takes `final_text` from the last result event, and `execute` appends it as the last assistant message. That message is what `model_graded_qa` reads as the answer.
- `.venv/lib/python3.*/site-packages/inspect_ai/scorer/_model.py`: `model_graded_qa`. Its `instructions`, `grade_pattern`, `model` and `model_role="grader"` arguments should be the only ones the spike needs.
- `spikes/capture_followups.py`: the spike shape to copy. It takes `--out <scratch>` for the model calls, has a separate `--analyse` step and gives usage in its docstring. Spikes have no tests here, so `python scripts/check.py task` is the gate.
- `docs/results/2026-11-12-improvement-report-runs.md`: the write-up shape for real runs, with the exact commands, who approved which run and a results table.

## Assumptions

- Which skill's report is graded? The example's `reviewer` agent, which is an agent, not a skill. It's the only thing in `examples/consumer` whose output is a report: `release-notes` writes a file and `debugging` changes code.
- Is the graded reply the subagent's report or the main agent's relay of it? It's the run's `final_text`, which is the main agent relaying the review. The subagent's own text isn't in the parent stream (see "Not yet done" in `docs/spike-findings.md`). Christopher marks the same text the grader sees.
- What counts as a pass? Every problem the reply reports names a file and a line in `till.py`. "How to fix it" isn't graded, so the rubric stays one thing both markers can agree on.
- Which grader model? `anthropic/claude-haiku-4-5`, the model every recent spike used, so 25 gradings stay well under a dollar. A second grader model is out.
- Who approves the collection runs? Christopher, before they run. 20 `claude-code` runs with Haiku cost more than the task's "under a dollar for grading", which only covers grading. If kept logs from earlier reviewer runs hold 20 usable replies, those get used instead.
- Can an agent fill in the marks? No. The task says the marks take Christopher's own time. AGENTS.md doesn't allow manual verification steps, but these marks are the spike's input, not a check that the work is done. The grade step refuses to run on empty marks, so an agent can't fill them in to get past it.
- Is the 90% bar right? Task 54 sets it before the spike, so the spike keeps it and doesn't revisit it.

## Owed

- There are no automated tests for criteria 2 and 4: refusing to grade while marks are empty, and calling the grader unusable when a control passes. Spikes in this repo have no tests, so those 2 behaviours are only checked by reading the code and the recorded run.
- Nothing checks the saved replies for secrets. They're reviews of a fixture with no `.env`, so the risk is low, but they're committed under `docs/results/` without the redaction `capture` uses.
