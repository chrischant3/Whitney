# Constraints

Last reviewed: 2026-11-02 by Christopher Chant

What "good enough to merge" means for this repo. Agents and people read this
before writing code; `scripts/check.py` runs it. If the two disagree, this file
wins and the script gets fixed. Loosening anything here needs its own commit
and a human reviewer, never the same commit as the change that was failing.

## Floor (always enforced, blocks)

- No new suppression comments: `# noqa`, `# type: ignore`, `# pragma: no cover`, `# pragma: no mutate`, `# nosec`, `nosemgrep`, `gitleaks:allow`
- No unimplemented stubs: `raise NotImplementedError`, `TODO`/`FIXME` in place of code, `except ...:` followed by a bare `pass`
- No skipped, xfailed or deleted tests, and no net removal of assertions from a test that stayed, without a reason in the commit message
- No secrets in source (the eval fixtures' fake canary tokens are the only allowed exception, see E1)
- This file does not get weakened to make a change pass

Checked by `python scripts/floor_guard.py` (diff since the merge base with
`origin/main`, including uncommitted and untracked files) and gitleaks.

Human decisions are recorded as commit-message trailers, so they show up in review:
- `Skip-reason: <test file> <why>` allows the skips added in that test file.
- `Exception-approved: E3` allows a new Exceptions row. Only a human adds this line.
- The same trailer allows a new entry in `.constraintsignore` (citing its ID inline, `path  # E3`) or `.gitleaksignore` (citing it in the comment line above the entry). Without it the entry is flagged, and a new `.constraintsignore` glob doesn't take effect.

## Enforced with numbers

Numbered checks **warn until 2026-11-16, then block** (`WARN_UNTIL` in
`scripts/check.py`). Two weeks to see real numbers before they gate anything.

| Dimension | Rule | Checked by | Runs at |
|-----------|------|-----------|---------|
| Tests | Whole suite passes (blocks now; a hard constraint in CLAUDE.md) | `pytest` | task end, CI |
| Secrets | No secrets in history or uncommitted changes (floor, blocks now) | `gitleaks git --redact` (+ `--pre-commit`) | every edit, CI |
| Coverage: changed lines | ≥ 80% of lines changed since `origin/main` covered | `pytest --cov` + `diff-cover --fail-under=80` | task end, CI |
| Mutation: touched functions | ≥ 60% of mutants in functions the diff touched are caught | `mutmut run` (incremental) + `scripts/check.py` | task end, CI |
| Dependencies | Nothing at high or above in the installed environment | `osv-scanner scan --no-resolve` on `pip freeze` | CI |

Why these numbers:
- **80% changed-line coverage**: high enough to force a test for new code, low enough to allow a config line or an error message.
- **60% mutation score on touched functions**: typical for a suite that has never been mutated; 80% is mature. It's scoped to functions a change touched, so you're judged on code you wrote, not on code you inherited.
- **High or above**: below that, dependency advisories are mostly noise for a local evaluation tool.

Mutation score = (killed + timeout) / (all mutants − segfaults). A segfault
means pytest crashed under the mutant, which says nothing about the tests
either way. "No tests" (a mutant no test reaches) counts as not caught.

## Measured, not yet enforced (ratchets)

Today's values. Checks compare against these, not an aspiration, and allow
0.5 percentage points of drift. When a number improves, update it in its own
commit; when it drops, that's the finding.

| Metric | Today | Direction | Checked by |
|--------|-------|-----------|------------|
| Project coverage | 92.6% | must not fall | `pytest --cov=whitney` |
| Mutation score (project) | 88.4% | must not fall | `mutmut run` |

Measured 2026-11-02: 2,191 mutants, 1,900 killed,
10 timeout, 203 survived, 48 not reached by any test, 30 segfault. First
baseline the same day was 77.4% coverage and 57.7% mutation score. Most
remaining survivors are equivalent mutants (an encoding name's case, a
timeout that never fires, `exist_ok` on folders that never pre-exist).

## Not covered, on purpose

- **Performance and accessibility budgets**: this is a CLI and library with no page to measure.
- **Lint and types**: not chosen for now; no linter or type checker is configured.
- **External opinion**: gitleaks and osv-scanner are judged outside this repo's own tests. Coverage and mutation are judged by our own suite, which is why mutation testing is here: it checks that the tests would notice a broken implementation.

## How to run

```bash
pip install -e ".[dev]"            # pytest-cov, diff-cover, mutmut; gitleaks and osv-scanner via brew
python scripts/check.py fast       # after an edit, a few seconds
python scripts/check.py task       # before handing work back, about 90 s (mutmut is incremental)
python scripts/check.py full       # CI: adds the dependency scan
```

Exit 0 pass (warnings allowed), 1 blocked, 2 a tool couldn't run. A 2 is never a pass.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| E1 | gitleaks `generic-api-key` | `tests/test_scoring.py` (`.gitleaksignore`) | `pk_canary_live_TEST_123` is a fake canary token; the scorer tests need a secret-shaped value to detect | Christopher Chant | 2027-01-31 |
| E2 | floor guard, all rules | `scripts/floor_guard.py`, `tests/test_floor_guard.py` (`.constraintsignore`) | They define and test the floor's own patterns, so they contain the strings they look for | Christopher Chant | 2027-01-31 |
