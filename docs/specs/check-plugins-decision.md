# Decision: check plugins (Task 57)

Write up the options for spec open question 9 (consumer projects adding their own checks), with their risks and grounded in how the registry, scorer and report work today. Then record Christopher's dated answer in the spec, and draft the build task in `tasks/plan-later.md` only if the answer is yes. Nothing gets built.

## Job story

When I have to answer spec open question 9, and spec assumption 5 says the only way to add a check is a pull request to Whitney
I want to see each way a consumer could add a check, and what each one costs in safety and in comparable results, in one place
So I can answer it once, on the record, without re-deriving the risks every time a team asks

## Acceptance criteria

- [ ] Given open question 9 in `docs/specs/package-and-expected-results.md`, when Christopher reads it after this task, then it lists at least 3 options: all checks stay in Whitney; other packages register checks through an entry point; checks load from the evals path, which spec assumption 2 already treats as trusted. Each option names its risks:
  - the consumer's code runs inside the scorer, in the same process that holds the run's secrets;
  - Whitney can't enforce its 6 "Rules for a new check" on code that lives outside it (positive evidence only, findable tokens, mutants killed, recorded harness fields, default minimum, a recorded real failure);
  - a plugin can take a name that clashes with a Whitney check;
  - it has to be settled whether a plugin's result counts in the gate.
- [ ] Given that write-up, when Christopher reads it, then it gives the number of known forks or consumer projects that would use a plugin, and names none of them.
- [ ] Given that write-up, when Christopher reads an option that allows plugins, then it says where the report would show that a result came from a plugin (the report header and `whitney checks`), and that today neither shows where a check came from.
- [ ] Given Christopher's answer, when anyone reads open question 9, then it reads "Answered <date> (Christopher):" followed by the answer. Spec assumption 5 and the "Not planned here" row of "Release scope" agree with it.
- [ ] Given any answer, when someone opens `tasks/plan-later.md`, then Task 57's box is ticked with the date. If the answer is yes, a new numbered task builds the feature. It has scope, acceptance criteria and verification in the file's shape, including a criterion that the report labels a plugin's result as one. If the answer is no, no task is drafted.
- [ ] Given the task's branch, when I run `git diff --stat` against the commit the branch started from, then only `docs/specs/package-and-expected-results.md` and `tasks/plan-later.md` have changed (this spec file itself, `docs/specs/check-plugins-decision.md`, may also appear if it is committed on the same branch).

## Where to look

Paths as of `b0627c6`. Where the code was, not what to build.

- `src/whitney/checks/__init__.py`: `Registry.add` (line 500) refuses a duplicate name. The last line (602) imports Whitney's own check modules by name, with no discovery step. Every plugin option has to change both.
- `src/whitney/tasks.py`: the scorer looks each check up by name and runs it in-process (line 777), next to `evidence.run_secrets`. `_check_record` (line 860) builds the per-result metadata that a "came from a plugin" field would join.
- `src/whitney/report.py`: `render_header` (line 718) is where an auditor sees Whitney's version and the eval files' hashes. A plugin's package and version would have to show there.
- `docs/spike-findings.md`: lines 629 and 888 show that a Kilo plugin runs where the login can be read and is handed the provider key. It's the closest evidence of what consumer code running in-process can reach.
- `tasks/plan-later.md`: Task 44 (line 112), the hooks safety spike, shows the shape a "where may consumer code run" recommendation takes. Task 54 (line 346) is the sibling decision that keeps a new kind of result out of the gate by default.
- `tests/test_checks.py`: the registry's tests, for whoever drafts the follow-on task. Run them with `python -m pytest -q tests/test_checks.py tests/test_cli_report_checks.py`.

## Assumptions

- Who decides? Christopher. The write-up recommends, and the session stops for his answer before writing "Answered" into the spec. If it's run unattended, it writes the options and the recommendation and leaves open question 9 unanswered.
- What does "spec, rule 5" in Task 57 mean? Spec assumption 5, "Checks live in Whitney" (line 37), since that's the one that points at open question 9. The "Rules for a new check" list also has a rule 5, about default minimums, and this isn't it.
- How many known forks would use it? None are recorded anywhere in the repo, so the write-up says 0 recorded and asks Christopher to correct it from what he knows. It doesn't query GitHub's list of forks, because a fork existing doesn't mean it wants plugins.
- Where does the write-up go? Inline under open question 9, like the other answered questions, not in a new file under `docs/results/`. Task 57 is size S and runs nothing.
- What to recommend? No for now: keep checks in Whitney. There are 0 known users, results stop being comparable once plugins exist, and the in-process risk stays unanswered until Task 44 reports. Christopher may overrule it.
