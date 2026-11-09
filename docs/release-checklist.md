# Release checklist

Work through this before tagging a release, and record the results in the table at the bottom. It's written for a CLI and library that teams fork, not a service: there's nothing to deploy or roll back. What can't be undone is publishing, since clones, forks and caches keep whatever was pushed, so the disclosure checks come first.

Claude Code users can run `/release <version>`, which works through these steps and reports what's left (`.claude/commands/release.md`).

## 1. Nothing goes public that shouldn't

- `gitleaks git --redact` finds nothing outside the Exceptions in CONSTRAINTS.md.
- `git ls-files logs results dataset` lists only `dataset/ifevalcode_prompt_injection_audit.md`. Eval logs hold transcripts, and the datasets are third-party.
- `git grep -n -E "/(Users|home)/$USER"` finds nothing: no paths from your own machine in docs, results or fixtures.
- Each finding in `docs/findings/` says whether the vendor has been told. Report it before the first release that includes it, or record why not (for example, the vendor's policy puts it out of scope).

## 2. The bar holds on the release commit

- `python scripts/check.py full` exits 0 in a fresh clone, installed as in CONSTRAINTS.md ("How to run"). Warnings are allowed; exit 2 is not a pass.
- That run includes mutation testing. `--skip-mutation` is a stopgap for day-to-day work, and its warning in place of the mutation scores is not a pass for a release.
- The `floor-guard` check passed on the pull request that made the release commit.
- No row in CONSTRAINTS.md's Exceptions table has expired, and each one is still needed.

## 3. The evidence still holds

- `whitney validate` exits 0 on Whitney's own template and on the consumer example:

  ```bash
  whitney validate --project-path template/kilo --evals-path evals --target kilo --constraints CONSTRAINTS.md
  whitney validate --project-path examples/consumer --evals-path examples/consumer/evals --target kilo
  ```

- For each target, run the evals with the default thresholds and the model you report results for. Both runs must exit 0:

  ```bash
  whitney run --target kilo --model kilo/anthropic/claude-haiku-4.5 \
      --project-path template/kilo --evals-path evals --epochs 3
  whitney run --target claude-code --model anthropic/claude-haiku-4-5 \
      --project-path template/claude-code --evals-path evals --epochs 3
  ```

  Save each report from `results/` under `docs/results/`. A `known_gap` scenario is reported, not gated, so check its finding is still open.
- If a template or Kilo's version changed since the last release, run [the VS Code extension check](vscode-manual-check.md) again and add a row to its results.

## 4. A new user can get started

- Clone the public repo into an empty folder. Follow the README from "Setup" to a first eval run, then the walkthrough in [adding-tests.md](adding-tests.md#walkthrough-test-a-new-rule), which adds an eval for a new rule to a copy of `examples/consumer`, using only the docs. Fix whatever doesn't work before tagging.
- After tagging, install from the tag into a new virtual environment, a Python install kept apart from your others. `whitney --version` must print the new version, and the two `whitney validate` commands in step 3 must exit 0.

## 5. The project files are current

- `version` in `pyproject.toml` is the new version. Bump the major part for a change that breaks forks or CI (a renamed option, a scorer that grades differently), the minor part for a new scenario, target or option, and the patch part for fixes.
- `CHANGELOG.md` has a section for the version: what changed, and what callers or CI must update.
- `SECURITY.md` says how to report a vulnerability privately.
- The GitHub repository description matches the README's first paragraph.

## 6. Optional: an independent security read

Before a release with large changes, have a reviewer that didn't write them audit the diff since the last tag: a person, or an AI security reviewer given only the diff. Fix what it finds first, or open an issue for each finding.

## 7. Tag and publish

Sign the tag with your own key, as for approvals in CONSTRAINTS.md:

```bash
VERSION=0.2.0
git tag -s "v$VERSION" -m "Whitney $VERSION"
git push origin "v$VERSION"
gh release create "v$VERSION" --verify-tag --title "Whitney $VERSION" --notes-file notes.md   # notes.md: the version's CHANGELOG section
```

## Results

| Date | Version | Who | 1. Disclosure | 2. `check.py full` | 3. Evals (kilo, claude-code) | 4. Fresh clone | 5. Project files | 6. Security read |
|---|---|---|---|---|---|---|---|---|
| | | | | | | | | |
