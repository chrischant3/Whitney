# Positive and negative trigger tests, gated separately (2026-11-11)

Evidence for Task 29 of the Phase 5 plan ([tasks/todo-phase5.md](../../tasks/todo-phase5.md)). No model was called: every figure here comes from a test or from `whitney report` on logs of earlier runs.

## What changed

The gate read a file's trigger tests as one pool. It now reads the template's positive trigger tests and its negative trigger tests each on their own, against the file's minimum and `--min-conclusive`, as it already read scenarios apart from trigger tests. A problem line names the part, and the line under a file's tables gives each part's count.

## The 12 of 12 and 0 of 3 case

No kept log has this outcome, so the logs are built by hand in the test, in the shape `whitney.tasks` writes: `test_positive_prompts_that_all_pass_dont_carry_negative_prompts_that_fail` in `tests/test_trigger.py`, and through the command in `test_whitney_report_fails_a_file_whose_negative_prompts_fail_though_its_positive_ones_all_pass` in `tests/test_cli_report_checks.py`.

| | Problems from the gate | The line under the tables |
|---|---|---|
| Before | none, so exit 0 | The gate reads the template's scenarios and its trigger tests separately: scenarios 3/3, trigger tests 12/15. |
| After | `release_notes: template negative trigger tests 0% is below the minimum 80%`, so exit 1 | The gate reads the template's scenarios, its positive trigger tests and its negative trigger tests separately: scenarios 3/3, positive trigger tests 12/12, negative trigger tests 0/3. |

## Red, then green

Each new test, and what it gave before the code changed. All 14 pass now.

| Test | Before the change |
|---|---|
| `test_positive_prompts_that_all_pass_dont_carry_negative_prompts_that_fail` | `assert [] == [...]`: "Right contains one more item: 'release_notes: template negative trigger tests 0% is below the minimum 80%'" |
| `test_negative_prompts_that_all_pass_dont_carry_positive_prompts_that_fail` | the same, for "template positive trigger tests 0%" |
| `test_both_kinds_falling_short_are_named_positive_first` | "At index 0 diff: 'release_notes: template trigger tests 17% is below the minimum 80%' != 'release_notes: template positive trigger tests 50% is below the minimum 80%'" |
| `test_the_gate_line_gives_a_rate_for_each_kind_of_prompt` | the report ended "scenarios 3/3, trigger tests 12/15." |
| `test_each_kind_of_prompt_needs_enough_conclusive_runs_of_its_own` | `assert [] == [...]`: missing "only 1/4 template positive trigger test runs were conclusive, below the minimum 50%" |
| `test_a_kind_with_no_conclusive_run_fails_though_the_other_passed` | `assert [] == [...]`: missing "template negative trigger tests had no conclusive runs, so they are below the minimum 80%" |
| `test_a_file_with_one_kind_of_prompt_is_gated_on_that_kind_only` | the report ended "scenarios 3/3, trigger tests 3/3." |
| `test_a_file_with_no_scenarios_and_one_kind_of_prompt_names_only_that_kind_under_its_table` | the report ended "The gate reads the template's trigger tests: 3/3." |
| `test_positive_prompts_that_ran_only_in_the_control_fail_the_gate_though_the_negatives_passed` | `assert [] == [...]`: missing "template positive trigger tests had no conclusive runs" |
| `test_a_file_with_no_scenarios_is_gated_on_each_kind_of_prompt` | `assert [] == [...]`: missing "template negative trigger tests 67% is below the minimum 80%" |
| `test_a_known_gap_among_the_prompts_stays_out_of_its_kinds_rate` | the report ended "scenarios 3/3, trigger tests 6/6." |
| `test_min_conclusive_given_to_the_gate_is_each_kinds_bar` | "'release_notes: only 7/8 template trigger test runs were conclusive, below the minimum 100%' != '... only 3/4 template positive trigger test runs ...'" |
| `test_whitney_report_fails_a_file_whose_negative_prompts_fail_though_its_positive_ones_all_pass` | `assert 0 == 1`: the command exited 0 |
| `test_both_kinds_of_prompt_passing_pass_the_gate` | passed before too. It guards against a gate that fails everything. |

## `whitney report` on the logs of two real runs

Both runs' logs were still in their scratch folders. The command was `whitney report <logs> --results-dir <folder>`, run with the code before the change and after it.

| Run | Before | After |
|---|---|---|
| [2026-11-10, Kilo 7.8.18](2026-11-10-skills-agents.md), 57 runs | exit 1 | exit 1, the same two problem lines |
| [2026-11-11, Claude Code 2.1.322](2026-11-11-claude-code-skills-agents.md), 17 runs | exit 1 | exit 1, one problem line changed |

Kilo, problems before and after:

```text
- release_notes: template scenarios 50% is below the minimum 80%
- reviewer: template scenarios 67% is below the minimum 80%
```

Kilo, the lines under the tables. Before: "scenarios 3/6, trigger tests 12/12" and "scenarios 4/6, trigger tests 9/9". After: "scenarios 3/6, positive trigger tests 6/6, negative trigger tests 6/6" and "scenarios 4/6, positive trigger tests 6/6, negative trigger tests 3/3".

Claude Code, problems before:

```text
- release_notes: template scenarios 50% is below the minimum 80%
- release_notes: template trigger tests 75% is below the minimum 80%
```

and after:

```text
- release_notes: template scenarios 50% is below the minimum 80%
- release_notes: template negative trigger tests 50% is below the minimum 80%
```

Claude Code, the lines under the tables. Before: "scenarios 1/2, trigger tests 3/4" and "scenarios 1/1, trigger tests 3/3". After: "scenarios 1/2, positive trigger tests 2/2, negative trigger tests 1/2" and "scenarios 1/1, positive trigger tests 2/2, negative trigger tests 1/1".

Both write-ups now say this.

## What this doesn't show

- **No real run changed its exit code.** Both kept runs failed the gate before and fail it now. The change from exit 0 to exit 1 is shown only on hand-built logs.
- **No real run has the 12 of 12 and 0 of 3 outcome.** A negative prompt has failed once in a real run, on Claude Code, and it failed on its `owner` not loading, not on the skill under test loading.
- **Small numbers.** A file with one negative prompt and three epochs fails at 2 of 3. Whether the minimum is right for a kind with few prompts is a judgement this task didn't make; `docs/adding-tests.md` now warns about it.
- **A negative prompt's owner.** A negative prompt with an `owner` fails when the owner doesn't load, and that counts against the negative trigger tests' rate. That was true before and is unchanged.
- **`whitney run`.** Only `whitney report` was run. Both use `check_thresholds`, and a test covers the report command; no run was made, because a run costs money.

## Review

A self-review of the diff, not a second reader's.

- `report.TRIGGER_PART` is gone, replaced by `TRIGGER_PARTS` and `GATE_PARTS`. Nothing in the repository used it outside `report.py`. A fork that imported it would break. Not kept as an alias, since the single pooled part no longer exists.
- The line under the tables is now built from the parts' names, so it can't drift from the gate's parts. `_gate_note` still accepts the old two-part names, and its existing test passes unchanged.
- The parts are ordered by `GATE_PARTS`, not alphabetically, so problems read scenarios, positive, negative.
- `check_thresholds` needed no change beyond its docstring: it already judged each part `summarise` gives it.

## The gate

Twelve existing tests in `tests/test_trigger.py` expect the pooled gate, or use a shared helper whose negative prompt passes 2 of 3 runs, which is 67% and now under the minimum. They are left as they were and fail, until the maintainer confirms each new expectation. So `python scripts/check.py task --base origin/main` blocks on the commit: 12 failed, 2,691 passed.

With the proposed expectations applied to the working tree and not committed, the same command passed: 2,703 tests, changed-line coverage 100%, touched-function mutation score 99.4% of 338 mutants. Its two survivors are in lines of `render_markdown` this change didn't touch. No CI run yet: the branch isn't pushed.
