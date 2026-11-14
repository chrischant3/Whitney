# Pull request risk score

`score.py` reads a unified diff on stdin and prints one JSON object: a score,
a level (`low` 0 to 2, `medium` 3 to 5, `high` 6 or more) and the rules that
fired. The same diff always gives the same JSON.

It is the `risk-score` skill's script from thoughtbot's `rails-consultant`
plugin 2.1.0 (https://github.com/thoughtbot/skills), copied unchanged and used
under its MIT licence (`LICENSE` here). Its rules were written for Rails
projects, so some of them (`migration`, `destructive_migration`) will rarely
fire on Whitney.

`.github/workflows/risk-score.yml` runs it on every pull request into main and
labels the pull request `risk:low`, `risk:medium` or `risk:high`.

The workflow scores a pull request with the base branch's copy of `score.py`,
so a pull request can't change its own rules (the pull request that first adds
the file is the one exception, and its summary says so). A pull request from a
fork is scored in the job summary but not labelled, since it gets no write
token. If the diff or the scorer fails, the job fails; it never labels
`risk:low` by default.

## Run it yourself

```bash
git -c core.quotePath=false diff --no-color --no-ext-diff --find-renames origin/main...HEAD \
  | python3 .github/risk/score.py
```

## Change a rule

Don't edit `score.py` to make one pull request score differently. Change it in
a pull request of its own, with a test in `tests/test_risk_score.py`, and say in
the description why the rule was wrong.

## Decisions

Decided 2026-11-13 (Christopher), when this was added:

- **The script is copied in, not installed.** CI needs no API key, no model call and no outside repository, and the score can't change under us. The cost is that upstream fixes don't arrive on their own: to take one, copy the new `score.py` over this one in a pull request of its own. Installing the plugin's repository at a pinned commit in the workflow would also work, at the price of depending on that repository being reachable.
- **The workflow labels and never blocks.** The label tells a reviewer how much care a pull request needs. A rule that failed high-scoring pull requests would stop most large ones here, and a maintainer can't approve their own, so there would be no one to act on a "second reviewer" rule.
- **It uses `pull_request`, not `pull_request_target`,** so code from a fork never runs next to a write token. The price is that a fork's pull request is scored but not labelled. `floor-guard.yml` uses `pull_request_target` because it must not be editable by the change it judges. The score only informs, so the weaker guard (the base branch's `score.py`, read by SHA) is enough.

## After it merges

Open the next pull request into main and check, in its Actions run:

- the `Risk score` job passes and its summary shows the JSON, with no "No score.py on main yet" note (that note only belongs on the pull request that adds the script);
- the pull request carries exactly one of `risk:low`, `risk:medium` or `risk:high`, matching the summary's level.

A pull request from a fork gets the summary but no label; that is expected.

## Rolling back

Revert the commit that added `.github/workflows/risk-score.yml`; nothing else depends on it. Labels already on pull requests stay, and are harmless. To remove them, run `gh label delete risk:low`, `gh label delete risk:medium` and `gh label delete risk:high`.
