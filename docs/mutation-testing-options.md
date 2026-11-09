# Mutation testing: why it was slow, and the options

Investigated 2026-11-09 on branch `claude/beautiful-albattani-5f1pgq`. Ask: mutation testing takes too long and uses too much CPU and memory; look for new options.

## How this was measured

A from-scratch `mutmut run` (what `check.py full` does) on a 4-core, 15 GB Linux cloud machine, Python 3.13, mutmut 3.8.1, every extra installed, with free memory sampled every 5 seconds. It ran as an unprivileged user, because as root `test_a_folder_the_snapshot_cant_list_in_full_is_incomplete` can read a `chmod 000` folder and fails. One test, `test_report.py::test_records_from_a_real_inspect_log`, was deselected for these runs only: Inspect's `mockllm` downloads a tiktoken file from a host this machine's network blocks. Neither affects the comparison, since both runs had the same setup. Nothing here was measured on a Mac.

## What was wrong

Most of the cost was not the tests. It was 26 mutants out of 6,874 (0.4%) that turn a loop in a parser into one that never ends, such as `i += 1` becoming `i += 2` in `static_checks._drop_comments`, `_drop_trailing_commas` and `_string_end`, `scoring._unquote`, `evidence._is_regular` and `project_files._instruction_matches`. Each one appends to a list until something stops it.

mutmut's only stop is its own wall-clock limit, (the covering tests' recorded time + 1) × 15. The parser is reached by 364 tests, so that limit was 213 seconds per mutant. Those 26 mutants took 57% of all mutant time (66 of 114 minutes summed across the workers). While they ran, their lists grew to 5 to 7 GB each, and with four workers the machine's memory peaked at 14.6 GB of 15. The kernel's out-of-memory killer took 14 of them. mutmut records a SIGKILL as a segfault, which the score leaves out, so those mutants silently dropped out of the project score.

That likely explains the memory complaint on the Mac too, though it is unmeasured there: macOS has no cgroup OOM killer, so the same mutants would push the machine into swap and memory compression until mutmut's 213-second limit, and show up as timeouts rather than segfaults.

Normal mutants never needed that much room. Across every mutant that was killed or survived, the longest took 4.8 times its tests' recorded time plus one second, and 99.9% took under 2.3 times.

## What changed

`[tool.mutmut] pytest_add_cli_args = ["--timeout=30"]`, with `pytest-timeout==2.4.0` added to the `dev` extra and the lock. Only mutmut's own pytest runs get it; a plain `pytest` or `check.py task` run has no per-test timeout. A looping mutant now fails the test it hangs after 30 seconds, which mutmut counts as killed. `tests/test_check.py::test_a_mutant_that_loops_forever_fails_its_test_in_seconds_not_minutes` holds the setting in place.

| From-scratch `mutmut run`, 4 cores | Before | After |
|---|---|---|
| Wall time | 31.4 min | 16.0 min |
| Mutant time, summed over workers | 114.3 min | 55.1 min |
| Peak memory used (whole machine) | 14.6 GB | 7.3 GB |
| Mean memory used | 5.1 GB | 2.1 GB |
| Longest mutant | 217 s | 40 s |
| Killed by the OOM killer (recorded as segfault) | 14 | 0 |
| Timeouts | 17 | 2 |
| Survivors | 32 | 32, the same ones |

Every changed verdict is a timeout or an OOM kill that became a kill, plus one kill that became a timeout; both count as caught. The project score rises slightly, because the 14 mutants that were left out as segfaults are now counted as caught.

The risk is a sound test that runs past 30 seconds on a slow or loaded machine: pytest-timeout would fail it, and mutmut would call a mutant killed that wasn't. The slowest test takes about 6.5 seconds alone, and the slowest that mutmut ever runs against a mutant about 3.5 seconds, so 30 seconds leaves a wide margin. A test that timed out with no mutant at all would also fail mutmut's clean run, which stops the whole run.

Regenerating the lock also added the hashed entries for `pyyaml-ft` and `zipfile-zstd` (Python before 3.14) that it was missing. Without them `pip install --require-hashes -r requirements.lock` stops on Python 3.13.

## Other options, and what I recommend

**Lower the per-test timeout to 10 or 15 seconds.** The looping mutants still cost 30 seconds each: 31 mutants over 25 seconds make up 16 of the remaining 55 summed minutes. At 10 seconds that saves about 3 minutes of wall time on 4 cores. I didn't do it because the margin over the slowest tests shrinks to about three times, and on the Mac `full` runs at background priority on the efficiency cores. Worth doing once someone has timed the slowest tests under those conditions.

**Reuse results across full runs (plan Phase 5, decision 2, step 1).** Still the biggest remaining lever for the everyday case, since most full runs follow small diffs. mutmut 3.8.1 still has the stale test map that made `full` delete `mutmut-stats.json`: an incremental run re-collects stats only for new tests, and editing a test doesn't invalidate cached results (mutmut issue #626, open since 2026-11-04). There is no mutmut release after 3.8.1, so this has to be built in `check.py` as planned.

**Faster tests (step 2).** After the fix, the worst case is dominated by a few end-to-end tests that reach many functions. `test_evidence.py::test_a_fake_agent_that_prints_its_key_and_writes_it_to_a_file_leaves_no_trace_in_the_log` alone is 27% of the worst case, `test_safe_staging.py::test_the_project_is_unchanged_after_a_run` 8%, and the `test_tasks.py::test_the_wrappers_give_todays_score_answers_on_the_same_runs` cases about 26% between them. The plan's profiling (3.6 of 5.5 seconds spent waiting on child processes) still applies.

**Run `full` in CI, sharded.** CI runs only the floor guard, so nothing but a developer's machine runs the whole mutation suite. A GitHub Actions matrix that runs `mutmut run "whitney.<module>*"` per group of modules, then merges the `.meta` files, would take it off the laptop entirely. It costs Actions minutes, and any shard needs the full stats run first, so four shards won't be four times faster.

**`--max-children`.** Peak memory is now 7.3 GB with four workers here. If the Mac still struggles, `check.py` could pass a lower `--max-children` (a command-line flag only, not a config key). Wall time grows in proportion.

**Not worth it here:**

- `mutate_only_covered_lines`: there were no "no tests" mutants, so it would remove nothing.
- `type_check_command`: needs mypy or pyrefly, and the project has no type checker on purpose (CONSTRAINTS.md, "Not covered").
- `process_isolation = "forkserver"`: added in 3.8.0 for fork-unsafe libraries such as gevent. It doesn't claim to be faster or lighter, and nothing here is fork-unsafe.
- `timeout_multiplier`: with the per-test timeout, mutmut's own limit matters only for a hang outside a test, so lowering it buys little.
- Sampling a share of mutants: it changes what the project ratchet means and would need its own decision. Not for the gate.

**Other tools.** None beats mutmut for this suite.

- pytest-gremlins 1.13 (alpha) runs every mutant in a new subprocess since 1.10.1, so each would pay Inspect's import again.
- cosmic-ray 8.8 has no per-mutant test selection: it runs the whole test command for every mutant, which is hours here.
- irradiate 0.4 (alpha, Rust core) is the only one with something mutmut lacks: memory-based worker recycling, `--diff` and sampling. Worth a spike only if mutmut stalls.

## Next steps

1. Run `check.py full` on the Mac with this branch, to confirm the drop there and record the new score in CONSTRAINTS.md's ratchet in its own commit.
2. Then step 1 (result reuse) as already planned.
3. Then decide on a shorter per-test timeout and on a CI mutation job.
