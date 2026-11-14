# `file_added`: a check for something the agent must do, 2026-11-13

Task 52 of the Later list ([tasks/plan-later.md](../../tasks/plan-later.md)) added `file_added`, the first file check that fails when the agent *didn't* do something (`skill_loaded` and `subagent_delegated` already did, for skills and subagents): it fails when no new file matches a pattern, and with `near`, when no new file sits beside a file the run added or changed. Turned round, a rule such as "colocate tests next to the source" could only be tested as "no test file in a separate folder", which a run that writes no test at all also passes.

- **The rule**, in [`examples/colocated-tests/AGENTS.md`](../../examples/colocated-tests/AGENTS.md): put each new test file next to the module it tests (`shop/test_pricing.py` for `shop/pricing.py`), not under `tests/`.
- **The fixture**, `shop`: `shop/pricing.py`, `shop/cart.py`, and older tests under `tests/`, `tests/test_cart.py` and `tests/test_pricing.py`. The existing `tests/` folder pulls an agent without the rule towards it.
- **The eval file**, [`evals/agents-md.json`](../../examples/colocated-tests/evals/agents-md.json): one prompt, "Add a function discount(amount, percent) to shop/pricing.py that returns amount with percent taken off, and write a test for it.", expecting `{"check": "file_added", "paths": ["*test_*.py"], "near": ["shop/*.py"]}`.
- **The control** is the baseline, with `AGENTS.md` removed.

The run used Kilo 7.8.27 and `kilo/anthropic/claude-haiku-4.5` through the Kilo Gateway, in local execution, from branch `task-52`. The logs were kept in a scratch folder and are not committed; `<scratch>` below stands for it.

## What was run

```bash
whitney validate --target kilo --project-path examples/colocated-tests --evals-path examples/colocated-tests/evals
whitney validate --target claude-code --project-path examples/colocated-tests --evals-path examples/colocated-tests/evals
whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
  --project-path examples/colocated-tests --evals-path examples/colocated-tests/evals \
  --epochs 1 --log-dir <scratch>/logs --results-dir <scratch>/results
```

Both `validate` commands ended `examples/colocated-tests/evals: 1 eval file(s), 0 problem(s), 0 warning(s)` and exited 0. `whitney run` exited 0. One epoch: one scenario, two variants, two agent runs.

## The result

| Scenario | baseline | template | Δ |
|---|---|---|---|
| add-discount | 0/1 | 1/1 | +100% |

That is the report's table, under the heading "AGENTS.md rule followed: a new test sits next to its module: added / (added + not_added)". `whitney run`'s progress output gave each variant's means: `not_added 1.000` for the baseline and `added 1.000` for the template.

The explanations, read from the logs with `inspect_ai.log.read_eval_log` (they are also in `inspect view`, under the score; the report itself doesn't print them):

- **Baseline, not added:** "no new file matches \*test_\*.py". The agent added `discount` to `shop/pricing.py` and its test to the existing `tests/test_pricing.py`, then ran it with pytest. It wrote no new test file, so neither `paths` nor `near` was met.
- **Template, added:** "1 new file(s) match \*test_\*.py beside a changed file matching shop/\*.py: project/shop/test_pricing.py beside shop/pricing.py". The agent edited `shop/pricing.py` and wrote `shop/test_pricing.py`, in four tool calls. It didn't run the tests, and left `tests/test_pricing.py` as it was; the check doesn't run tests either.

The plain control failed, so the opposite-rule control the task allows ("put tests under `tests/`") wasn't needed and wasn't run.

## What it cost

The agent runs outside Inspect, so Inspect's usage fields are empty. Kilo's own `cost` on each `step_finish` event in the stream, summed: $0.050 for the baseline and $0.034 for the template, **$0.084** in all. Most of it is cache reads and writes (166,788 and 20,079 tokens in the baseline; 53,716 and 18,709 in the template).

## What this doesn't show

- One epoch, one scenario, one model, one harness version: it shows the check can fail and pass on a real run, not how often the rule holds.
- Only `kilo` was run. `file_added` reads the fixture's snapshots, which both targets take the same way, and the example validates for `claude-code`, but no `claude-code` run was made (no `ANTHROPIC_API_KEY` here). One epoch there is two runs, a few cents.
- The check doesn't read the new file, so an empty `shop/test_pricing.py` would pass. Pair it with another check if the content matters.
- `near` was met by a changed source file here. A run that adds a new source file and its test together is covered by unit tests only.
