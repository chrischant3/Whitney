# Reading the results

Read this when the person has run the evals and asks what the results mean. Work through the report in this order.

1. **Read the explanation of every failure**, with `inspect log dump <log>` or `inspect view`. For each, decide whether the agent broke the rule or the scenario is wrong. A file the project's commands wrote, such as a lockfile, is the scenario's fault: narrow `paths` and say so. Don't count it as the rule failing.
2. **Read the Δ column before the template column.** A rule that passes as often in the baseline as in the template isn't doing anything: the model behaves that way already. Report it as "held, but not because of the rule", not as a rule that works.
3. **For a skill, read the line under the scenarios table** that says in how many runs it loaded. A skill that didn't load can't have been followed. If it rarely loads, its `description` is the thing to fix.
4. **Read the replies of the failing runs** to see how the rule gave way: time pressure, a claimed authority, a workaround. That says what to change in the rule's wording. A rule that only says what not to do often holds better once it says what to do instead, such as "describe the change, then stop and wait".
5. **Compare the targets for `AGENTS.md`.** The same file can hold on one harness and fail on the other with the same model, because each frames the file differently. Kilo puts it at the end of its system prompt under a bare `Instructions from:` line, and Claude Code says it overrides the defaults (`docs/results/2026-11-10-kilo-prompt-capture.md` in the checkout). Run both before rewording a rule for one of them.
6. **Rerun with `--epochs 3`** before drawing conclusions. One run per scenario shows which rules are weak, not how weak.
7. **Read "How to improve the files under test"**, the section just before "Evaluation Instructions and Assets". Keep its two halves apart when you report them: what the runs showed, and what the file's text suggests, which is only a suggestion when no run failed. Its hook advice is labelled "not tested by Whitney"; say so, and say what the report says a hook can do, before anyone adds one ("Reading the improvement report" in `docs/adding-tests.md`).

When rewording a rule, keep the old scenarios unchanged, so the comparison with the last run is fair.
