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
- No inline `gitleaks:allow` in any file, not just Python: gitleaks honours it everywhere
- No lowered eval-gate thresholds: the `MIN_*` constants in `src/whitney/report.py` and the `--min-*` defaults in `scripts/run_evals.py`
- No new `known_gap` scenario, which the eval gate leaves out of its thresholds, without an Exceptions row it cites (`"known_gap": "E3: ..."`)
- This file does not get weakened to make a change pass

Checked by `python scripts/floor_guard.py` (diff since the merge base with
`origin/main`, including uncommitted and untracked files) and gitleaks, and
again by CI on every pull request into main (below).

Human decisions are recorded as commit-message trailers, so they show up in review:
- `Skip-reason: <test file> <why>` allows the skips added in that test file.
- `Exception-approved: E3` allows a new Exceptions row. Only a human adds this line.
- The same trailer allows a new entry in `.constraintsignore` (citing its ID inline, `path  # E3`) or `.gitleaksignore` (citing it in the comment line above the entry). Without it the entry is flagged, and a new `.constraintsignore` glob doesn't take effect.
- A `.constraintsignore` path is exempt from the pattern rules (suppressions, stubs, skips) only. A test there still can't lose assertions or be deleted.
- Anyone who can commit can write a trailer, agents included. Branch protection with a required human review is what makes the approval a human's.
- A maintainer can't approve their own pull requests, so on a repository with one, a signature does that job: an approval counts only when the commit carrying it is signed by an approver. On every pull request into main, CI (`.github/workflows/floor-guard.yml`, check `floor-guard`) runs main's copy of the floor guard and of `scripts/check_approvals.py`. GitHub must show two kinds of commit as verified, and committed by an approver: each commit with an `Exception-approved:` trailer, and each commit that changes the workflow, the floor guard or the approval check. Approvers are the repository variable `APPROVERS`, comma-separated logins, or the repository's owner.
- Sign approvals with a key on your GitHub account that an agent can't use, such as one that asks for a passphrase or a touch each time: `git commit --amend -S --no-edit --trailer "Exception-approved: E3"`.
- The check only blocks once `floor-guard` is a required status check in the branch ruleset. An agent holding a token with admin rights on the repository could change the ruleset itself, so give agents a token without them.

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
A segfault can also be mutmut's own crash: on macOS, a child forked from a
multi-threaded process crashes if it asks the system for proxy settings,
which Python does when an HTTP client is built and no proxy variable is
set. `tests/conftest.py` sets `no_proxy` for every non-e2e test so that
lookup never happens; a segfault in `mutmut results` is worth a look.

## Measured, not yet enforced (ratchets)

Today's values. Checks compare against these, not an aspiration, and allow
0.5 percentage points of drift. When a number improves, update it in its own
commit; when it drops, that's the finding.

| Metric | Today | Direction | Checked by |
|--------|-------|-----------|------------|
| Project coverage | 98.7% | must not fall | `pytest --cov=whitney` |
| Mutation score (project) | 99.6% | must not fall | `mutmut run` |

Measured 2026-11-03, with every extra installed from `requirements.lock`:
2,854 mutants, 2,822 killed, 20 timeout, 12 survived, none unreached, no
segfaults. The 12 survivors are equivalent mutants that no test can tell
apart: a codec name's case (`"utf-8"` and `"UTF-8"`), `bytes.decode()`'s
default, two no-op defaults in `agent._append_final_message`, and
`PWD=str(None)` for Claude Code, which the shell replaces with the real
folder. Earlier baselines: 77.4% coverage and 57.7% mutation score, then
92.6% and 88.4% (2026-11-02).

Equivalent mutants found since, on 2026-11-06 (the same kinds, in code added
later): more codec-name cases (`project_files._load_kilo`, `_read_text`,
`evalfile.fixture_files`, `validate.exception_ids`);
`_linked_below(None, ...)` in `not_staged_reason`, `_walk_files`,
`_direct_md_files` and `_is_kilo_file`, which walks on past the project root
to `/` but finds no symlink there, since the root is already resolved; and
`allow_nan=None` in `variants.apply_variant`, which `json` reads as `False`.
Task 9 adds more codec-name cases (`agent.login_secrets`, `evidence._safe`
twice and `evidence._text`); `$0` written as `XXshXX` or `SH` in
`evidence.git_state`, the name `sh -c` gives the script, which
`GIT_STATE_SCRIPT` never reads, so it can reach only sh's own messages on
stderr, which nothing parses; the `", "` in `tasks._scenario_task`'s "it
applies to" message, which joins a single name whenever that message is
reached, since a target missing from a file's `targets` leaves room for only
the other of the two; and `simplefilter("ignore")` without `FutureWarning`
in `tasks._loaded`, whose block can raise no warning but version 1's
`FutureWarning`. None of these depends on the platform.
Two more survive here but are not equivalent: `re` flag `1` in place of `0`
in `_glob_parts` is a bit Python 3.13 and later give no meaning, but on 3.11
and 3.12 it is `re.TEMPLATE`, matching raises `re.error`, and
`test_names_match_whatever_their_case_only_where_kilos_glob_does` kills it;
and `harbor_tasks._host_variables` opening `DOCKER-COMPOSE.YAML` finds the
same file on a case-insensitive disk such as macOS's, but is killed on a
case-sensitive one (Linux).

## Not covered, on purpose

- **Performance and accessibility budgets**: this is a CLI and library with no page to measure.
- **Lint and types**: not chosen for now; no linter or type checker is configured.
- **External opinion**: gitleaks and osv-scanner are judged outside this repo's own tests. Coverage and mutation are judged by our own suite, which is why mutation testing is here: it checks that the tests would notice a broken implementation.

## How to run

```bash
pip install --require-hashes -r requirements.lock && pip install --no-deps -e .   # every extra; gitleaks and osv-scanner via brew
python scripts/check.py fast       # after an edit, a few seconds
python scripts/check.py task       # before handing work back, about 90 s (mutmut is incremental)
python scripts/check.py full       # CI: adds the dependency scan
```

Install every extra, as above: without `openai` the gateway tests skip, its
mutants go untested, and the mutation score falls below its ratchet.

Exit 0 pass (warnings allowed), 1 blocked, 2 a tool couldn't run. A 2 is never a pass.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| E1 | gitleaks `generic-api-key` | `tests/test_scoring.py` (`.gitleaksignore`) | `pk_canary_live_TEST_123` is a fake canary token; the scorer tests need a secret-shaped value to detect | Christopher Chant | 2027-01-31 |
| E2 | floor guard, pattern rules (suppressions, stubs, skips) | `scripts/floor_guard.py`, `tests/test_floor_guard.py` (`.constraintsignore`) | They define and test the floor's own patterns, so they contain the strings they look for | Christopher Chant | 2027-01-31 |
| E3 | eval gate thresholds (`known_gap`) | `evals/configuration/kilo-jsonc.json`, scenario `bash-symlink` | Kilo finding 001: a symlink made through the bash tool bypasses `external_directory`, so the scenario fails until Kilo fixes it; it's reported, not gated | Christopher Chant | 2027-01-31 |
