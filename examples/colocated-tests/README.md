# Colocated tests example

A project with one rule that says what to do, not what not to do, and the eval that tests it with `file_added`:

- `AGENTS.md`: put each new test file next to the module it tests (`shop/test_pricing.py` for `shop/pricing.py`), not under `tests/`. Kilo reads it, and so does Claude Code, since the project has no `CLAUDE.md`.
- `kilo.jsonc`: holds nothing but its `$schema`. Kilo needs a project config to run in.
- `evals/agents-md.json`: one prompt that asks for a function in `shop/pricing.py` and a test for it. Its check, `{"check": "file_added", "paths": ["*test_*.py"], "near": ["shop/*.py"]}`, passes only if a new test file sits in the same folder as a source file under `shop/` that the run added or changed. Its baseline removes `AGENTS.md`.
- `evals/fixtures/shop/`: the project the agent works in. Its older tests are under `tests/`, so an agent without the rule has a reason to put the new test there, and the baseline can fail.

Run every command below from the root of the Whitney checkout.

Check the eval without calling a model (each ends `1 eval file(s), 0 problem(s), 0 warning(s)` and exits 0):

    whitney validate --target kilo --project-path examples/colocated-tests --evals-path examples/colocated-tests/evals
    whitney validate --target claude-code --project-path examples/colocated-tests --evals-path examples/colocated-tests/evals

Run it (this calls a real model and costs money; one epoch is two agent runs, about 8 cents on Haiku through the Kilo Gateway in the [recorded run](../../docs/results/2026-11-13-file-added.md)):

    whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 --project-path examples/colocated-tests --evals-path examples/colocated-tests/evals --epochs 1

In that run the template added `shop/test_pricing.py` and passed, and the baseline added its test to the existing `tests/test_pricing.py` and failed.
