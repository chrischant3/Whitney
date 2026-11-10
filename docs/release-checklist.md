# Release checklist

Work through this before tagging a release, and record the results in the table at the bottom. It's written for a CLI and library that teams fork, not a service: there's nothing to deploy or roll back. What can't be undone is publishing, since clones, forks and caches keep whatever was pushed, so the disclosure checks come first.

Claude Code users can run `/release <version>`, which works through these steps and reports what's left (`.claude/commands/release.md`).

## 1. Nothing goes public that shouldn't

- `gitleaks git --redact` finds nothing outside the Exceptions in CONSTRAINTS.md.
- `git ls-files logs results dataset` lists only `dataset/ifevalcode_prompt_injection_audit.md`. Eval logs hold transcripts, and the datasets are third-party.
- `git grep -n -E "/(Users|home)/$USER"` finds nothing: no paths from your own machine in docs, results or fixtures.
- Each finding in `docs/findings/` says whether the vendor has been told. Report it before the first release that includes it, or record why not (for example, the vendor's policy puts it out of scope).

## 2. The bar holds on the release commit

- The "Mutation (full)" workflow passes on the release commit. It runs `python scripts/check.py full` in a fresh clone on GitHub. Start it from the Actions tab, or with the command below, and wait for it to finish. Warnings are allowed; exit 2 is not a pass.

  ```bash
  gh workflow run mutation-full.yml --ref main
  gh run list --workflow mutation-full.yml --limit 1
  ```

  If the run fails on the mutation score, open its log: the line starting `[fail] mutation score` names the survivors. Kill them or explain them in CONSTRAINTS.md, then run it again.
- Don't run `check.py full` on a laptop in its place. On a Mac at background priority its tests run past mutmut's time limit, and the run stops before it judges a mutant (CONSTRAINTS.md, "How to run").
- That run includes mutation testing. A run with `--skip-mutation` prints a warning in place of the mutation scores, and is not a pass for a release.
- The `floor-guard` check passed on the pull request that made the release commit.
- No row in CONSTRAINTS.md's Exceptions table has expired, and each one is still needed.

## 3. The evidence still holds

- `whitney validate` exits 0 on Whitney's own templates and on the consumer example:

  ```bash
  whitney validate --project-path template/kilo --evals-path evals --target kilo --constraints CONSTRAINTS.md
  whitney validate --project-path template/claude-code --evals-path evals --target claude-code
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
- After tagging, install from the tag into a new virtual environment, a Python install kept apart from your others. `whitney --version` must print the new version, and the `whitney validate` commands in step 3 must exit 0.

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
| 2026-11-10 | 0.2.0 | Christopher Chant; checks run by Claude Code | Pass: gitleaks clean; only the audit file tracked; no machine paths; finding 001 reported as Kilo-Org/kilocode#16028 | Pass on `35eeaa4` (mutation 99.5%, coverage 99.6%); not re-run on the release commit (Christopher) | Pass: kilo 15/18 rule, 15/15 permission; claude-code 16/18 (Haiku, 3 epochs). VS Code extension check moved to the next release (Christopher) | Pass after fixes: a fresh clone found the README's install broken and step 7 needing a Kilo login, both fixed before the tag; install from the tag still to check | Pass | Not done (optional) |
