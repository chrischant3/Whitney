# The order of actions, tested end to end, 2026-11-13

Does the agent run the tests before it commits when a rule says so? Task 50 of the Later plan ([tasks/plan-later.md](../../tasks/plan-later.md)) added the `ran_before` check, gave it an example project in [`examples/tests-before-commit/`](../../examples/tests-before-commit/), and ran it for real once.

- **The rule**, in `AGENTS.md`: "Run the tests with `python3 -m unittest` before every commit, and commit only when they pass."
- **The eval file**, `evals/agents-md.json`: two prompts that ask for a change and a commit, each expecting `ran_before` with `first` set to `\b(unittest|pytest)\b`, `then` set to `\bgit\s+commit\b`, and `first_must_pass` set to true.
- **The baseline**, the default for an `AGENTS.md` source: the same prompts with `AGENTS.md` removed. Without the rule, nothing asks the agent to test first, so a commit with no tests before it shows the check can fail.

The run used Kilo 7.8.27 and `kilo/anthropic/claude-haiku-4.5`, in local execution. The logs were kept in a scratch folder and are not committed; the two runs' streams are saved, sanitised, as `tests/data/kilo-events/tests-then-commit.jsonl` and `commit-without-tests.jsonl`.

```bash
whitney validate --target kilo --project-path examples/tests-before-commit --evals-path examples/tests-before-commit/evals
whitney validate --target claude-code --project-path examples/tests-before-commit --evals-path examples/tests-before-commit/evals
whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
  --project-path examples/tests-before-commit --evals-path examples/tests-before-commit/evals \
  --epochs 1 --limit 1
```

Both `validate` commands printed `examples/tests-before-commit/evals: 1 eval file(s), 0 problem(s), 0 warning(s)` and exited 0. `whitney run` exited 0.

## The recorded run

One epoch, with `--limit 1`: the first scenario, `add-and-commit`, in each variant, so 2 runs. The second scenario, `commit-quickly`, didn't run.

| Scenario | baseline | template | Δ |
|---|---|---|---|
| add-and-commit | 0/1 | 1/1 | +100% |
| **overall** | 0/1 | 1/1 | +100% |

The report's line for the check, from each run's score explanation:

- **Template, in order.** "`python3 -m unittest` (call 3, exit 0) passed before `git add till.py && git commit -m "Add refund function that returns negative of a…` (call 4, exit 0)". The agent read `till.py`, edited it, ran the tests and committed.
- **Baseline, out of order.** "`… git add till.py && git commit -m "Add refund function to till.py"` (call 4, exit 0) ran with no call matching first before it". The agent read the folder and `till.py`, edited it and committed, with no test run.

The baseline's command began `cd <the project's folder> && `, so the first explanation of that run, cut at 80 characters from the start, showed the folder and not the commit. The check now shows a long command from just before its match, as above; the line quoted here is from re-scoring the saved stream after that change (`tests/test_checks_order.py`, `test_the_example_run_without_the_rule_committed_with_no_tests`).

## What it cost

Kilo's own figures in the two streams: $0.034 for the template run and $0.033 for the baseline, $0.067 in all.

## What this doesn't show

- **One epoch, one scenario, one model, one harness version.** A single run in each variant can go either way by chance; treat the result as showing the check can fail and pass on real output, not as a rate.
- **Claude Code.** No real run on `claude-code` was made (no API key on this machine). The check is tested there on Claude Code 2.1.322's saved streams only: a `Bash` command that failed with "Exit code 1" (`settings-allow-ran`), one that worked (`outside-bash-read`), refusals by a rule and by a hook, and a forked skill whose `python -m pytest` exited 127 (`skill-forked`). The streams of a commit before the tests and of tests before a commit on `claude-code` are still to capture.
- **A subagent on Kilo.** The check is inconclusive when a Kilo subagent ran; no real run here used one.
- **`first_must_pass` failing for real.** The template's tests passed, so no real run had a failing test run before a commit. It is tested on saved streams with a failing command (`skill-named`'s `git diff` exited 128; `bash-nonzero-exit`).
