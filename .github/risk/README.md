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
