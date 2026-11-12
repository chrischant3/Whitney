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
- No eval-gate threshold the floor guard can't read: in `src/whitney/thresholds.py` each `MIN_*` is bound once, by a plain top-level `MIN_NAME = number` line; `scripts/run_evals.py`, `src/whitney/report.py` and `src/whitney/cli.py` bind a `MIN_*` name only by importing it under its own name; and a `--min-*` default in them is left out, `None`, a `MIN_*` constant, or a number no lower than before (the guard parses these files at the merge base and now)
- No new `known_gap` scenario, which the eval gate leaves out of its thresholds, without an Exceptions row it cites (`"known_gap": "E3: ..."`)
- This file does not get weakened to make a change pass

Checked by `python scripts/floor_guard.py` (diff since the merge base with
`origin/main`, including uncommitted and untracked files) and gitleaks. CI
runs the floor guard again on every pull request into main (below), and
gitleaks with the rest of `check.py task` (`.github/workflows/checks.yml`).

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
| Tests | Whole suite passes (blocks now; a hard constraint in CLAUDE.md) | `pytest` | task end |
| Secrets | No secrets in history or uncommitted changes (floor, blocks now) | `gitleaks git --redact` (+ `--pre-commit`) | every edit |
| Coverage: changed lines | ≥ 80% of lines changed since `origin/main` covered | `pytest --cov` + `diff-cover --fail-under=80` | task end |
| Mutation: touched functions | ≥ 95% of mutants in functions the diff touched are caught | `mutmut run` on the touched functions' mutants (`check.py task`) or on all of them (`check.py full`) + `scripts/check.py` | task end and on every pull request in CI (`check.py task`); weekly in CI (`check.py full`) |
| Dependencies | Nothing at high or above in the installed environment | `osv-scanner scan --no-resolve` on the installed distributions | weekly in CI (`check.py full`), and before a pull request that changes `requirements.lock` |

Why these numbers:
- **80% changed-line coverage**: high enough to force a test for new code, low enough to allow a config line or an error message.
- **95% mutation score on touched functions**: this project's score is 98 to 99.7%, so the 60% it started at (typical for a suite that has never been mutated) let new code with many surviving mutants through every pull request while the project score fell; the full run on main at `1ef449b` measured 98.0% after Tasks 17 to 25. Raised by Christopher Chant on 2026-11-10. It's scoped to functions a change touched, so you're judged on code you wrote, not on code you inherited. An equivalent mutant in a touched function is explained in this file, as the others are, or the change waits.
- **High or above**: below that, dependency advisories are mostly noise for a local evaluation tool.

Mutation score = (killed + timeout) / (all mutants − segfaults). A segfault
means pytest crashed under the mutant, which says nothing about the tests
either way. "No tests" (a mutant no test reaches) counts as not caught.
A segfault can also be mutmut's own crash: on macOS, a child forked from a
multi-threaded process crashes if it asks the system for proxy settings,
which Python does when an HTTP client is built and no proxy variable is
set. `tests/conftest.py` sets `no_proxy` for every non-e2e test so that
lookup never happens; a segfault in `mutmut results` is worth a look.
A mutant that makes a loop endless can also end as a "segfault": its list
grows until the out-of-memory killer takes it, which mutmut records the same
way. `[tool.mutmut]` gives mutmut's pytest runs a 30-second per-test timeout
(pytest-timeout) so such a test fails, and the mutant is killed, long before
that (docs/mutation-testing-options.md).

## Measured, not yet enforced (ratchets)

Today's values. Checks compare against these, not an aspiration, and allow
0.5 percentage points of drift. When a number improves, update it in its own
commit; when it drops, that's the finding.

| Metric | Today | Direction | Checked by |
|--------|-------|-----------|------------|
| Project coverage | 99.3% | must not fall | `pytest --cov=whitney` |
| Mutation score (project) | 99.7% | must not fall | `mutmut run` rerunning every mutant in `check.py full`; `check.py task` judges mutmut's cached results |

Measured 2026-11-03, with every extra installed from `requirements.lock`:
2,854 mutants, 2,822 killed, 20 timeout, 12 survived, none unreached, no
segfaults. The 12 survivors are equivalent mutants that no test can tell
apart: a codec name's case (`"utf-8"` and `"UTF-8"`), `bytes.decode()`'s
default, two no-op defaults in `agent._append_final_message`, and
`PWD=str(None)` for Claude Code, which the shell replaces with the real
folder. Earlier baselines: 77.4% coverage and 57.7% mutation score, then
92.6% and 88.4% (2026-11-02).

Project coverage measured 99.36% on 2026-11-06, after Phase 5 Tasks 1 to 9, and recorded as 99.3%. The mutation score that day was 99.4%, below the recorded 99.6% but within its 0.5-point drift; every survivor is killed or listed below, and the fall comes from more equivalent mutants in new code, so the recorded figure stays.

The mutation score measured 99.7% on 2026-11-08, in a from-scratch `check.py full` on `phase5` at `5d8e7e3` (Tasks 1 to 10 and both mutant clean-ups), and is recorded as 99.7%.

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
`FutureWarning`. Task 10 adds `package_version("WHITNEY")` in
`tasks._whitney_version`, which finds the same installed package, since
`importlib.metadata` matches a package's name whatever its case. Task 11
adds more codec-name cases (`cli.harness_version`, `cli.write_report`);
the `", "` in `tasks._scenario_task` moved to `tasks.why_not_applies`, which
`whitney run` uses too, and an eval file may now list each target only once.
The IFEvalCode escape fix (2026-11-12) adds `simplefilter("ignore", )`
without its category in `interference._parses` and
`interference._decode_escapes`: each block runs only `ast.parse` or a
`unicode_escape` decode, which raise no warning but the `SyntaxWarning` or
`DeprecationWarning` already named, so ignoring every warning there ignores
the same one.
None of these depends on the platform.
One more survives here but is not equivalent: `re` flag `1` in place of `0`
in `_glob_parts` is a bit Python 3.13 and later give no meaning, but on 3.11
and 3.12 it is `re.TEMPLATE`, matching raises `re.error`, and
`test_names_match_whatever_their_case_only_where_kilos_glob_does` kills it.
`harbor_tasks._host_variables` opening `DOCKER-COMPOSE.YAML`, which finds the
same file on a case-insensitive disk such as macOS's, survived here too until
2026-11-07; `test_the_compose_file_is_read_by_its_exact_name_on_a_case_sensitive_disk`
now kills it on every disk by simulating a case-sensitive one.

## Not covered, on purpose

- **Performance and accessibility budgets**: this is a CLI and library with no page to measure.
- **Lint and types**: not chosen for now; no linter or type checker is configured.
- **External opinion**: gitleaks and osv-scanner are judged outside this repo's own tests. Coverage and mutation are judged by our own suite, which is why mutation testing is here: it checks that the tests would notice a broken implementation.

## How to run

```bash
pip install --require-hashes -r requirements.lock && pip install --no-deps -e .   # every extra; gitleaks and osv-scanner via brew
python scripts/check.py fast       # after an edit, a few seconds
python scripts/check.py task       # before handing work back: about a minute for a small diff, longer as the diff grows (mutmut runs only the touched functions' mutants)
python scripts/check.py full       # reruns every mutant, and the dependency scan; run it on GitHub, not on a laptop (below)
```

`task` runs the test suite with `pytest -n auto` (pytest-xdist, in the `dev`
extra for this reason): on every core it takes about 30 seconds instead of
one to two minutes on one. pytest-cov combines the workers' coverage, and the
figure matches a serial run. mutmut's pytest runs get no `-n`, since mutmut
runs its own workers; a plain `pytest` stays on one process too.

CI runs on GitHub's hosted runners, which cost nothing for a public
repository. Every pull request into main gets `check.py task`
(`.github/workflows/checks.yml`), starting warm from the last weekly full
run's mutmut results, so it mutates only the functions the diff touches.
`check.py full` runs from scratch every Sunday and on demand from the Actions
tab (`.github/workflows/mutation-full.yml`); a fall in the project score fails
that run, and GitHub emails the owner. Neither workflow edits this file: a
person records a new ratchet value, in its own commit.

Run `full` on GitHub only (Christopher, 2026-11-10): `gh workflow run
mutation-full.yml --ref <branch>`, or "Run workflow" on the Actions tab. On a
Mac at background priority the slower tests run past mutmut's 30-second limit,
and the run stops before it judges a mutant. Start one on your branch before a
pull request that changes `requirements.lock`, so the dependency scan sees the
new versions before the weekly run does, and on main before a release. The ratchet's mutation score
was measured on a Mac; once the weekly run on main has a result, re-baseline it
from that run and say so here, since macOS and Linux differ by a few survivors.
`task` mutates only the functions the diff touches and judges them as above;
its project mutation score is mutmut's cached results, the touched functions'
fresh ones and every other function's last one, labelled as cached. It still
enforces the ratchet, since each of those results was measured; mutants not
checked since their function changed are left out and counted, and when they
are more than 5% of all mutants, or there are no cached results, a score that
holds only warns. `full` deletes mutmut's map of which tests reach which
function (`mutants/mutmut-stats.json`), because mutmut 3.8.1 adds a test to it
only when the test is new, then reruns every mutant against the current tests
and judges the ratchet from that, so a weakened or newly reaching test is
measured too.

A new worktree starts cold: its first `full` runs every mutant, and until
then `task`'s cached project score covers only the mutants run so far. To start
warm, copy the main checkout's `mutants/` into it, delete the copied tests
(mutmut copies `tests/` in again, and leftover test files from the main checkout
would otherwise run and kill mutants), then touch the sources:
`cp -R ../Whitney/mutants . && rm -rf mutants/tests && find src -name '*.py' -exec touch {} +`.
The copied verdicts were measured against the main checkout's tests, and stay
that way until `full` reruns them.
mutmut decides a file is unchanged by comparing modification times, and the
copy is newer than the sources, so without the touch it keeps the mutants of
whatever code the main checkout last mutated, even where that code has changed
since. After the touch it regenerates every file and keeps the results of
each function whose code is unchanged.

Install every extra, as above: without `openai` the gateway tests skip, its
mutants go untested, and the mutation score falls below its ratchet.

Exit 0 pass (warnings allowed), 1 blocked, 2 a tool couldn't run. A 2 is never a pass.

## Exceptions

| ID | Rule | Path | Reason | Owner | Expires |
|----|------|------|--------|-------|---------|
| E1 | gitleaks `generic-api-key` | `tests/test_scoring.py` (`.gitleaksignore`) | `pk_canary_live_TEST_123` is a fake canary token; the scorer tests need a secret-shaped value to detect | Christopher Chant | 2027-01-31 |
| E2 | floor guard, pattern rules (suppressions, stubs, skips) | `scripts/floor_guard.py`, `tests/test_floor_guard.py` (`.constraintsignore`) | They define and test the floor's own patterns, so they contain the strings they look for | Christopher Chant | 2027-01-31 |
| E3 | eval gate thresholds (`known_gap`) | `evals/configuration/kilo-jsonc.json`, scenario `bash-symlink` | Kilo finding 001: a symlink made through the bash tool bypasses `external_directory`, so the scenario fails until Kilo fixes it; it's reported, not gated | Christopher Chant | 2027-01-31 |
