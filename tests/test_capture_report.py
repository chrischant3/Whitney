"""The report's "What was sent to the model" section (Phase 5 Task 31, decision 6)."""

from types import SimpleNamespace

from whitney.capture_report import HEADING, render_capture_markdown


def log(variant, runs, *, source="AGENTS.md", target="kilo", role=None, eval_file="rules.json", status="success"):
    """A log of one eval file's variant whose samples' scores keep `runs`, each a score's metadata."""
    samples = [
        SimpleNamespace(id=f"s{index}", error=None, scores={"scenario_checks": SimpleNamespace(answer="pass", metadata=metadata)})
        for index, metadata in enumerate(runs)
    ]
    metadata = {
        "eval_file": eval_file, "source": source, "target": target, "variant": variant,
        "variant_role": role or ("template" if variant == "template" else "baseline"),
    }
    return SimpleNamespace(eval=SimpleNamespace(task=f"t_{variant}", metadata=metadata), samples=samples, status=status)


FIRST = {"in": "first", "field": "system prompt", "heading": None}


def sent(**found):
    """A score's `sent` entry: each label found in the first model call (0, any request index) or not at all (None)."""
    places = {label: FIRST if where is not None else {"in": None} for label, where in found.items()}
    return {"sent": {"via": "proxy", "requests": 3, "not_model_calls": 0, "found": places}}


def test_logs_without_capture_or_thinking_add_no_section():
    assert render_capture_markdown([log("template", [{"checks": []}])]) == ""
    assert render_capture_markdown([]) == ""


def test_an_instruction_file_found_in_the_template_and_not_in_the_baseline():
    report = render_capture_markdown([
        log("baseline", [sent(**{"AGENTS.md": None})]),
        log("template", [sent(**{"AGENTS.md": 0}), sent(**{"AGENTS.md": 1})]),
    ])

    assert report.startswith(f"## {HEADING}\n")
    assert (
        "- `rules.json` on kilo: instruction file `AGENTS.md`, looked for as its exact text after normalising line endings "
        "and trailing spaces. Only what the first model call held counts as sent as an instruction.\n"
        "  - baseline: `AGENTS.md` not in the first model call in 1 of 1 runs.\n"
        "  - template: `AGENTS.md` in the first model call in 2 of 2 runs (system prompt).\n"
    ) in report


def test_on_claude_code_agents_md_stands_for_the_staged_instruction_files_and_their_imports():
    report = render_capture_markdown([log("template", [sent(**{"CLAUDE.md": 0, "docs/style.md": None})], target="claude-code")])

    assert (
        "- `rules.json` on claude-code: `AGENTS.md` stands for the instruction files staged and the files they import, "
        "each looked for as its exact text after removing block-level HTML comments, as Claude Code does, and normalising "
        "line endings and trailing spaces. Only what the first model call held counts as sent as an instruction.\n"
        "  - template: `CLAUDE.md` in the first model call in 1 of 1 runs (system prompt); "
        "`docs/style.md` not in the first model call in 1 of 1 runs.\n"
    ) in report


def test_a_skill_s_description_and_body_are_counted_separately():
    report = render_capture_markdown([log("template", [sent(description=0, body=None)], source=".kilo/skills/notes/SKILL.md")])

    assert (
        "- `rules.json` on kilo: skill `.kilo/skills/notes/SKILL.md`, its description and its body looked for separately. "
        "Only what the first model call held counts as sent as an instruction.\n"
        "  - template: description in the first model call in 1 of 1 runs (system prompt); body not in the first model call in 1 of 1 runs.\n"
    ) in report


def test_a_config_is_not_sent_as_text():
    report = render_capture_markdown([log("template", [sent()], source="kilo.jsonc")])

    assert "- `rules.json` on kilo: `kilo.jsonc`: not sent as text: enforced by the harness." in report


def test_a_file_whose_runs_were_not_captured_says_so():
    report = render_capture_markdown([
        log("template", [sent(**{"AGENTS.md": 0})]),
        log("template", [{"thinking": {"set": "x", "sent": "not recorded (no --capture-prompt)", "text": "in the events"}}], eval_file="other.json"),
    ])

    assert "- `other.json` on kilo: not captured." in report


def test_some_runs_of_a_variant_not_captured_are_counted():
    report = render_capture_markdown([log("template", [sent(**{"AGENTS.md": 0}), {"checks": []}])])

    assert "  - template: `AGENTS.md` in the first model call in 1 of 1 runs (system prompt) (1 run not captured).\n" in report


def test_a_variant_with_no_run_captured_says_not_captured():
    report = render_capture_markdown([log("baseline", [{"checks": []}]), log("template", [sent(**{"AGENTS.md": 0})])])

    assert "  - baseline: not captured.\n  - template: `AGENTS.md` in the first model call in 1 of 1 runs (system prompt).\n" in report


def test_inspect_s_unlogged_calls_are_named():
    run = {"sent": {"via": "inspect-log", "requests": 1, "found": {"AGENTS.md": FIRST}, "unlogged": 2}}

    report = render_capture_markdown([log("template", [run])])

    assert "  - template: `AGENTS.md` in the first model call in 1 of 1 runs (system prompt); Inspect logged no call for 2 model events.\n" in report


def test_thinking_is_one_line_per_file_and_target_with_each_value_counted():
    local = {"set": "kilo run --variant high --thinking", "sent": '"reasoning": {"effort": "high"}', "text": "in the events"}
    other = {**local, "text": "none in the events"}

    report = render_capture_markdown([log("template", [{"thinking": local}, {"thinking": local}, {"thinking": other}])])

    assert (
        "- `rules.json` on kilo, thinking: asked for: `kilo run --variant high --thinking` (3 runs); "
        "reached the model: `\"reasoning\": {\"effort\": \"high\"}` (3 runs); thinking text: in the events (2 runs), none in the events (1 run)."
    ) in report


def test_a_docker_run_s_thinking_says_what_was_asked_for_and_what_was_applied():
    docker = {"asked": "no thinking level", "applied": "no thinking setting in the request", "text": "none in the events"}

    report = render_capture_markdown([log("template", [{"thinking": docker}])])

    assert (
        "- `rules.json` on kilo, thinking: asked for: no thinking level (1 run); reached the model: no thinking setting in the request (1 run); "
        "thinking text: none in the events (1 run)."
    ) in report


def test_variants_are_in_role_order_and_errored_or_failed_logs_are_skipped():
    errored = SimpleNamespace(id="e", error="boom", scores=None)
    template = log("template", [sent(**{"AGENTS.md": 0})])
    template.samples.append(errored)
    report = render_capture_markdown([
        log("zz_control", [sent(**{"AGENTS.md": None})], role="control"),
        template,
        log("aa_baseline", [sent(**{"AGENTS.md": None})]),
        log("broken", [sent(**{"AGENTS.md": 0})], status="error"),
    ])

    lines = report.splitlines()
    assert lines.index("  - aa_baseline: `AGENTS.md` not in the first model call in 1 of 1 runs.") < next(
        index for index, line in enumerate(lines) if line.startswith("  - template:")
    ) < lines.index("  - zz_control: `AGENTS.md` not in the first model call in 1 of 1 runs.")
    assert "broken" not in report


def test_a_score_whose_entries_are_not_records_counts_as_not_captured():
    bad = {"sent": {"via": "proxy", "found": ["not", "a", "mapping"]}, "thinking": "not a record"}

    report = render_capture_markdown([log("template", [bad]), log("baseline", [sent(**{"AGENTS.md": None})])])

    assert "  - template: not captured" in report
    assert "thinking" not in report


def test_a_file_with_no_source_has_nothing_to_look_for():
    report = render_capture_markdown([log("template", [sent()], source=None)])

    assert "- `rules.json` on kilo: no `source` to look for." in report
