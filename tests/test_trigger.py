"""The `trigger` block of a skill's or an agent's eval file (spec, "Saying which variants run" and "Check details").

Its prompts expand into scenarios that expect the skill and subagent checks.
Trigger tests never run in a baseline; positive prompts also run in a control
Whitney adds itself, which removes the skill or agent; negative prompts run
in the template only.
"""

import json
from pathlib import Path

import pytest

from whitney import tasks
from whitney.evalfile import EvalFileError, load, under_test
from whitney.validate import validate_evals, validate_project
from whitney.variants import TRIGGER_CONTROL, declared_variants, runs_in

MODEL = "test-provider/test-model"
SKILL = ".kilo/skills/release-notes/SKILL.md"
AGENT = ".kilo/agents/reviewer.md"
POSITIVE = [{"id": "asks-for-release-notes", "prompt": "Write the release notes for this version."}]
NEGATIVE = [
    {"id": "asks-for-a-bug-fix", "prompt": "Fix the failing test in tests/test_app.py.", "owner": "debugging"},
    {"id": "asks-the-time", "prompt": "What time is it?"},
]


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use for {name}.\n---\n\n# {name}\n"


@pytest.fixture
def project(tmp_path):
    """A consumer project with two skills and two agents, and its evals root with one fixture."""
    root = tmp_path / "my-config"
    files = {
        "AGENTS.md": "- Never commit .env files or secrets\n",
        "kilo.jsonc": "{}",
        ".kilo/skills/release-notes/SKILL.md": skill_md("release-notes"),
        ".kilo/skills/release-notes/template.md": "# Release notes\n",
        ".kilo/skills/debugging/SKILL.md": skill_md("debugging"),
        ".kilo/agents/reviewer.md": "---\ndescription: Reviews code.\nmode: subagent\n---\nReview the code.\n",
        ".kilo/agents/planner.md": "---\ndescription: Plans work.\nmode: subagent\n---\nPlan the work.\n",
        "evals/fixtures/repo/project/README.md": "# App\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def eval_file(source=SKILL, **overrides):
    return {
        "version": 2,
        "source": source,
        "defaults": {"fixture": "repo", "expect": [{"check": "no_file_edits"}]},
        "scenarios": [{"id": "writes-notes", "prompt": "Draft the notes."}],
        "trigger": {"positive": POSITIVE, "negative": NEGATIVE},
    } | overrides


def write(project, data, name="skills/release-notes.json"):
    path = project / "evals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def problems(raised):
    return [(p.location, p.message) for p in raised.value.problems]


# --- Which sources can have trigger tests -----------------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        (".kilo/skills/release-notes/SKILL.md", ("skill", "release-notes")),
        (".kilo/agents/reviewer.md", ("agent", "reviewer")),
        ("AGENTS.md", None),
        (".kilo/rules/data.md", None),
        (".kilo/skills/release-notes/notes.md", None),
        (".kilo/skills/SKILL.md", None),
        (".kilo/skills/a/b/SKILL.md", None),
        (".kilo/agents/team/reviewer.md", None),
        (".kilo/agents/reviewer.txt", None),
        ("x/.kilo/agents/reviewer.md", None),
        (".kilo/skills/release-notes/SKILL.md.bak", None),
    ],
)
def test_a_source_is_a_skill_an_agent_or_neither(source, expected):
    assert under_test(source) == expected


# --- Expansion ------------------------------------------------------------------------------


def test_a_skills_prompts_expand_after_its_scenarios_positives_first(project):
    scenarios = load(write(project, eval_file())).resolved()

    assert [(s.id, s.trigger, s.prompt) for s in scenarios] == [
        ("writes-notes", None, "Draft the notes."),
        ("asks-for-release-notes", "positive", "Write the release notes for this version."),
        ("asks-for-a-bug-fix", "negative", "Fix the failing test in tests/test_app.py."),
        ("asks-the-time", "negative", "What time is it?"),
    ]


def test_a_skills_prompts_expect_the_skill_checks_and_an_owner_adds_its_load(project):
    scenarios = {s.id: s for s in load(write(project, eval_file())).resolved()}

    def expect(scenario_id):
        return [(e.check, e.params) for e in scenarios[scenario_id].expect]

    assert expect("writes-notes") == [("no_file_edits", {})]
    assert expect("asks-for-release-notes") == [("skill_loaded", {"skill": "release-notes"})]
    assert expect("asks-for-a-bug-fix") == [
        ("skill_not_loaded", {"skill": "release-notes"}),
        ("skill_loaded", {"skill": "debugging"}),
    ]
    assert expect("asks-the-time") == [("skill_not_loaded", {"skill": "release-notes"})]


def test_an_agents_prompts_expect_the_subagent_checks(project):
    negative = [{"id": "asks-for-a-plan", "prompt": "Plan the release.", "owner": "planner"}]
    path = write(project, eval_file(AGENT, trigger={"positive": POSITIVE, "negative": negative}), "agents/reviewer.json")

    scenarios = {s.id: [(e.check, e.params) for e in s.expect] for s in load(path).resolved()}

    assert scenarios["asks-for-release-notes"] == [("subagent_delegated", {"agent": "reviewer"})]
    assert scenarios["asks-for-a-plan"] == [
        ("subagent_not_delegated", {"agent": "reviewer"}),
        ("subagent_delegated", {"agent": "planner"}),
    ]


def test_trigger_tests_take_their_fixture_setup_and_pressure_from_defaults(project):
    setup = [{"step": "git_init"}, {"step": "git_commit", "files": ["."], "message": "Start"}]
    data = eval_file(defaults={"fixture": "repo", "pressure": "time", "setup": setup, "expect": [{"check": "no_file_edits"}]})

    [positive] = [s for s in load(write(project, data)).resolved() if s.trigger == "positive"]

    assert (positive.fixture, positive.pressure, [step.step for step in positive.setup]) == ("repo", "time", ["git_init", "git_commit"])
    assert (positive.fixture_at, positive.expect_at, positive.index) == ("defaults.fixture", "trigger.positive[0]", 0)
    assert (positive.known_gap, positive.metadata) == (None, {})


def test_without_defaults_a_trigger_test_runs_under_no_pressure_after_one_git_init(project):
    [negative] = [s for s in load(write(project, eval_file(trigger={"negative": NEGATIVE[1:]}))).resolved() if s.trigger]

    assert (negative.pressure, [step.step for step in negative.setup], negative.index, negative.expect_at) == (
        "none", ["git_init"], 0, "trigger.negative[0]",
    )


def test_a_file_without_a_trigger_block_expands_nothing(project):
    data = eval_file()
    del data["trigger"]

    assert [(s.id, s.trigger) for s in load(write(project, data)).resolved()] == [("writes-notes", None)]


# --- What the schema refuses -----------------------------------------------------------------


def test_a_trigger_block_needs_a_skill_or_an_agent_as_its_source(project):
    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file("AGENTS.md"), "agents-md.json"))

    assert problems(raised) == [("trigger", (
        "trigger tests ask whether a skill or an agent was used, so source must be "
        ".kilo/skills/<name>/SKILL.md or .kilo/agents/<name>.md; got 'AGENTS.md'"
    ))]


def test_a_trigger_block_needs_a_fixture_in_defaults(project):
    data = eval_file(defaults={}, scenarios=[{"id": "a", "prompt": "p", "fixture": "repo", "expect": [{"check": "no_file_edits"}]}])

    with pytest.raises(EvalFileError) as raised:
        load(write(project, data))

    assert problems(raised) == [("trigger", "trigger tests take their fixture from defaults.fixture, which is missing")]


def test_a_trigger_block_needs_at_least_one_prompt(project):
    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file(trigger={})))

    assert problems(raised) == [("trigger", "Value error, give at least one positive or negative prompt")]


@pytest.mark.parametrize(
    "trigger, at",
    [
        ({"positive": [{"id": "Bad Id", "prompt": "p"}]}, "trigger.positive[0].id"),
        ({"positive": [{"id": "a", "prompt": "  "}]}, "trigger.positive[0].prompt"),
        ({"positive": [{"id": "a", "prompt": "p", "owner": "debugging"}]}, "trigger.positive[0].owner"),
        ({"negative": [{"id": "a", "prompt": "p", "owner": ""}]}, "trigger.negative[0].owner"),
        ({"negative": [{"id": "a", "prompt": "p", "fixture": "repo"}]}, "trigger.negative[0].fixture"),
        ({"positive": POSITIVE, "neutral": []}, "trigger.neutral"),
    ],
)
def test_a_malformed_prompt_is_named_where_it_is(project, trigger, at):
    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file(trigger=trigger)))

    assert [location for location, _ in problems(raised)] == [at]


def test_ids_are_unique_across_scenarios_and_prompts(project):
    trigger = {
        "positive": [{"id": "writes-notes", "prompt": "p"}, {"id": "b", "prompt": "p"}],
        "negative": [{"id": "b", "prompt": "p"}, {"id": "c", "prompt": "p"}],
    }

    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file(trigger=trigger)))

    assert problems(raised) == [
        ("trigger.positive[0].id", "duplicate scenario id 'writes-notes'"),
        ("trigger.negative[0].id", "duplicate scenario id 'b'"),
    ]


def test_an_owner_is_another_skill_than_the_one_under_test(project):
    trigger = {"negative": [{"id": "a", "prompt": "p", "owner": "release-notes"}]}

    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file(trigger=trigger)))

    assert problems(raised) == [("trigger.negative[0].owner", (
        "'release-notes' is the skill under test; an owner is another skill, the one that should load instead"
    ))]


def test_a_file_with_a_trigger_block_cant_declare_the_automatic_controls_name(project):
    data = eval_file(variants={TRIGGER_CONTROL: {"role": "control", "remove": ["AGENTS.md"]}})

    with pytest.raises(EvalFileError) as raised:
        load(write(project, data))

    assert problems(raised) == [(f"variants.{TRIGGER_CONTROL}", (
        "is the name of the control Whitney adds for a file's trigger tests; give this variant another name"
    ))]


def test_a_file_without_a_trigger_block_may_use_that_variant_name(project):
    data = eval_file("AGENTS.md", variants={TRIGGER_CONTROL: {"role": "control", "remove": ["AGENTS.md"]}})
    del data["trigger"]

    assert list(declared_variants(load(write(project, data, "agents-md.json")))) == [TRIGGER_CONTROL]


# --- Variants --------------------------------------------------------------------------------


def test_positive_prompts_add_a_control_that_removes_the_skills_folder(project):
    variants = declared_variants(load(write(project, eval_file())))

    assert list(variants) == ["baseline", TRIGGER_CONTROL]
    assert (variants[TRIGGER_CONTROL].role, variants[TRIGGER_CONTROL].remove) == ("control", [".kilo/skills/release-notes/"])
    assert variants[TRIGGER_CONTROL].set_pointers == {}


def test_an_agents_control_removes_its_file(project):
    path = write(project, eval_file(AGENT, trigger={"positive": POSITIVE}), "agents/reviewer.json")

    assert declared_variants(load(path))[TRIGGER_CONTROL].remove == [AGENT]


def test_the_control_is_added_beside_the_files_own_variants_even_when_it_declares_none(project):
    own = {"no_agents_md": {"remove": ["AGENTS.md"]}}

    assert list(declared_variants(load(write(project, eval_file(variants=own))))) == ["no_agents_md", TRIGGER_CONTROL]
    assert list(declared_variants(load(write(project, eval_file(variants={}))))) == [TRIGGER_CONTROL]


def test_negative_prompts_alone_add_no_control(project):
    path = write(project, eval_file(trigger={"negative": NEGATIVE}))

    assert list(declared_variants(load(path))) == ["baseline"]


@pytest.mark.parametrize(
    "variant, trigger, runs",
    [
        ("template", None, True),
        ("template", "positive", True),
        ("template", "negative", True),
        ("baseline", None, True),
        ("baseline", "positive", False),
        ("baseline", "negative", False),
        ("allow_control", None, True),
        ("allow_control", "positive", False),
        (TRIGGER_CONTROL, None, False),
        (TRIGGER_CONTROL, "positive", True),
        (TRIGGER_CONTROL, "negative", False),
    ],
)
def test_which_scenarios_run_in_which_variant(variant, trigger, runs):
    assert runs_in(variant, trigger, has_trigger=True) is runs


def test_in_a_file_without_trigger_tests_every_scenario_runs_in_every_variant():
    assert runs_in(TRIGGER_CONTROL, None, has_trigger=False) is True


# --- The tasks -------------------------------------------------------------------------------


def task_for(project, variant, name="skills/release-notes.json"):
    return tasks.scenario_eval(
        name, variant, target="kilo", model=MODEL, evals_path=str(project / "evals"), project_path=str(project),
    )


def test_the_template_runs_every_scenario_and_marks_the_trigger_tests(project):
    write(project, eval_file())

    samples = task_for(project, "template").dataset

    assert [(sample.id, sample.metadata.get("whitney_trigger")) for sample in samples] == [
        ("writes-notes", None),
        ("asks-for-release-notes", "positive"),
        ("asks-for-a-bug-fix", "negative"),
        ("asks-the-time", "negative"),
    ]
    assert "whitney_trigger" not in samples[0].metadata


def test_a_scenarios_own_metadata_cant_pass_for_the_mark(project):
    """Free-form metadata named like the mark, or `trigger`, leaves a scenario a scenario: it keeps its baseline."""
    noted = {"id": "writes-notes", "prompt": "Draft the notes.", "metadata": {"whitney_trigger": "negative", "trigger": "positive", "route": "x"}}
    write(project, eval_file(scenarios=[noted]))

    [sample] = task_for(project, "baseline").dataset

    assert (sample.id, sample.metadata.get("trigger"), sample.metadata.get("route")) == ("writes-notes", "positive", "x")
    assert "whitney_trigger" not in sample.metadata
    assert [s.id for s in task_for(project, TRIGGER_CONTROL).dataset] == ["asks-for-release-notes"]


def test_no_trigger_prompt_runs_in_a_baseline(project):
    write(project, eval_file())

    task = task_for(project, "baseline")

    assert [sample.id for sample in task.dataset] == ["writes-notes"]
    assert task.metadata["variant_role"] == "baseline"


def test_only_positive_prompts_run_in_the_control_and_the_skill_is_gone_there(project):
    write(project, eval_file())

    task = task_for(project, TRIGGER_CONTROL)

    assert [sample.id for sample in task.dataset] == ["asks-for-release-notes"]
    assert task.metadata["variant_role"] == "control"
    staged = task.metadata["evaluation_assets"]
    assert not [path for path in staged if path.startswith(".kilo/skills/release-notes/")]
    assert ".kilo/skills/debugging/SKILL.md" in staged


def test_the_task_records_what_is_under_test(project):
    write(project, eval_file())
    write(project, {"version": 2, "source": "AGENTS.md", "defaults": {"fixture": "repo", "expect": [{"check": "no_file_edits"}]},
                    "scenarios": [{"id": "a", "prompt": "p"}]}, "agents-md.json")

    assert task_for(project, "template").metadata["under_test"] == {"kind": "skill", "name": "release-notes"}
    assert task_for(project, "template", "agents-md.json").metadata["under_test"] is None


# --- Validation ------------------------------------------------------------------------------


def test_validation_reports_a_trigger_blocks_problems(project):
    path = write(project, eval_file("AGENTS.md"), "agents-md.json")

    assert [(p.file, p.location) for p in validate_evals(project / "evals").problems] == [(path, "trigger")]


def test_a_well_formed_skill_file_is_valid_against_its_project(project):
    write(project, eval_file())
    result = validate_evals(project / "evals")

    assert result.problems == []
    assert validate_project(result.files, project, "kilo", evals_path=project / "evals") == []


def test_a_skill_that_isnt_staged_is_named_at_source_for_the_control_too(project):
    path = write(project, eval_file(".kilo/skills/missing/SKILL.md", variants={}), "skills/missing.json")

    found = validate_project(validate_evals(project / "evals").files, project, "kilo", evals_path=project / "evals")

    assert [(p.file, p.location) for p in found] == [(path, "source"), (path, "source")]
    assert found[1].message.startswith("the control for the trigger tests removes the source: ")


def test_apply_pending_in_defaults_with_nothing_pending_is_named_once_for_a_trigger_test(project):
    setup = [{"step": "git_init"}, {"step": "git_commit", "files": ["."], "message": "Start"}, {"step": "apply_pending"}]
    data = eval_file(defaults={"fixture": "repo", "setup": setup, "expect": [{"check": "no_file_edits"}]},
                     scenarios=[{"id": "writes-notes", "prompt": "Draft.", "setup": []}], trigger={"positive": POSITIVE})
    write(project, data)

    found = [(p.location, p.message) for p in validate_evals(project / "evals").problems]

    assert [location for location, _ in found] == ["defaults.setup[2]"]
    assert found[0][1].endswith("(scenario 'asks-for-release-notes')")


def test_the_run_orders_the_control_last_and_leaves_a_skills_file_out_for_claude_code(project):
    from whitney import cli
    from whitney.checks import registry

    parsed = load(write(project, eval_file()))

    assert cli.variant_order(parsed) == ["baseline", "template", TRIGGER_CONTROL]
    assert cli.why_not_run(parsed, "kilo", registry()) is None
    assert "skill_loaded" in cli.why_not_run(parsed, "claude-code", registry())


def test_an_owner_must_be_a_staged_skill(project):
    trigger = {"negative": [{"id": "a", "prompt": "p", "owner": "refactoring"}, {"id": "b", "prompt": "p", "owner": "reviewer"}]}
    path = write(project, eval_file(trigger=trigger))

    found = validate_project(validate_evals(project / "evals").files, project, "kilo", evals_path=project / "evals")

    assert [(p.file, p.location, p.message) for p in found] == [
        (path, "trigger.negative[0].owner", "no staged skill is named 'refactoring'; staged skills: debugging, release-notes"),
        (path, "trigger.negative[1].owner", "no staged skill is named 'reviewer'; staged skills: debugging, release-notes"),
    ]


def test_an_agents_owner_must_be_a_staged_agent(project):
    trigger = {"negative": [{"id": "a", "prompt": "p", "owner": "debugging"}, {"id": "b", "prompt": "p", "owner": "planner"}]}
    path = write(project, eval_file(AGENT, trigger=trigger), "agents/reviewer.json")

    found = validate_project(validate_evals(project / "evals").files, project, "kilo", evals_path=project / "evals")

    assert [(p.file, p.location, p.message) for p in found] == [
        (path, "trigger.negative[0].owner", "no staged agent is named 'debugging'; staged agents: planner, reviewer"),
    ]


def test_a_skill_in_another_folder_kilo_loads_can_be_an_owner(project):
    (project / ".claude" / "skills" / "refactoring").mkdir(parents=True)
    (project / ".claude" / "skills" / "refactoring" / "SKILL.md").write_text(skill_md("refactoring"))
    write(project, eval_file(trigger={"negative": [{"id": "a", "prompt": "p", "owner": "refactoring"}]}))

    assert validate_project(validate_evals(project / "evals").files, project, "kilo", evals_path=project / "evals") == []


# --- The scorer records whether the skill or agent was used -----------------------------------

KILO_EVENTS = Path(__file__).parent / "data" / "kilo-events"


@pytest.mark.parametrize(
    "case, kind, name, used",
    [
        ("skill-named", "skill", "release-notes", True),
        ("skill-named", "skill", "debugging", False),
        ("skill-description", "skill", "debugging", None),  # a step hides its tool calls
        ("subagent-description", "agent", "q7-helper", True),
        ("subagent-description", "agent", "general", False),
        ("task-deny", "agent", "q7-helper", False),
        ("command-option", "skill", "release-notes", False),  # no tool call at all: nothing was loaded
    ],
)
def test_whether_the_skill_or_agent_under_test_was_used_is_read_from_the_stream(case, kind, name, used):
    from whitney.checks.skills import was_used

    assert was_used((KILO_EVENTS / f"{case}.jsonl").read_text(), kind, name) is used


def test_an_empty_stream_cant_say_whether_it_was_used():
    from whitney.checks.skills import was_used

    assert was_used("", "skill", "release-notes") is None


def test_the_score_says_whether_the_skill_under_test_was_used():
    from whitney.checks import Verdict, registry

    verdicts = [(registry()["no_file_edits"], Verdict("pass", "no watched file was added"))]
    run = {"events": (KILO_EVENTS / "skill-named.jsonl").read_text(), "exit_code": 0, "stderr": ""}

    def score(under_test):
        return tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], run, [], under_test=under_test)

    assert score(("skill", "release-notes")).metadata["used"] is True
    assert score(("skill", "debugging")).metadata["used"] is False
    assert "used" not in score(None).metadata


def test_a_run_of_the_agent_under_test_with_the_agent_option_used_it_unless_kilo_fell_back():
    """Such a run never delegates to the agent: it is the agent, so the `task` call was_used looks for isn't there."""
    from whitney.checks import Verdict, registry

    verdicts = [(registry()["no_file_edits"], Verdict("pass", "no watched file was added"))]
    events = (KILO_EVENTS / "agent-option-all.jsonl").read_text()

    def used(option, stderr="", name="a-reviewer"):
        run = {"events": events, "stderr": stderr, "exit_code": 0}
        return tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], run, [],
                                     under_test=("agent", name), option=option).metadata["used"]

    assert used({"agent": "a-reviewer"}) is True
    assert used({"agent": "a-reviewer"}, stderr="! agent \"a-reviewer\" not found. Falling back to default agent") is None
    assert used(None) is False  # the default agent ran, and never delegated to it
    assert tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], {"events": " \n", "exit_code": 1, "stderr": ""}, [],
                                 under_test=("agent", "a-reviewer"), option={"agent": "a-reviewer"}).metadata["used"] is None
    assert tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], {}, [],
                                 under_test=("agent", "a-reviewer"), option={"agent": "a-reviewer"}).metadata["used"] is None
    assert used({"agent": "someone-else"}) is False
    assert used({"command": "a-reviewer"}) is False
    assert tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], {"events": events, "exit_code": 0}, [],
                                 under_test=("skill", "a-reviewer"), option={"agent": "a-reviewer"}).metadata["used"] is False


def test_a_run_that_ended_early_without_using_it_might_have_gone_on_to():
    from whitney.checks import Verdict, registry

    verdicts = [(registry()["no_file_edits"], Verdict("pass", "no watched file was added"))]
    events = (KILO_EVENTS / "skill-named.jsonl").read_text()

    def used(under_test, **run):
        return tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], {"events": events, "stderr": ""} | run,
                                     [], under_test=under_test).metadata["used"]

    assert used(("skill", "debugging"), exit_code=1) is None
    assert used(("skill", "debugging"), exit_code=0, ended_early=True) is None
    assert used(("skill", "release-notes"), exit_code=1) is True
    assert tasks._scenario_score(verdicts, ["unchanged", "edited", "inconclusive"], [], {}, [], under_test=("skill", "x")).metadata["used"] is None


# --- The report ------------------------------------------------------------------------------


def log(variant, role, scenario, outcomes, *, trigger=None, used=None, kind="skill", name="release-notes"):
    """A log as scenario_eval writes it for a skill's (or an agent's) eval file.

    `used` is one value per run: True, False, "?" for a run whose stream couldn't say, or None for a score that says nothing.
    """
    from types import SimpleNamespace

    metadata = {
        "variant": variant, "variant_role": role, "eval_name": "release_notes", "eval_file": "skills/release-notes.json",
        "title": "Release notes skill", "labels": ["pass", "fail", "inconclusive"],
        "checks": {"skill_loaded": {"labels": ["loaded", "not_loaded", "inconclusive"], "judges": "agent", "min_pass_rate": 0.8}},
        "min_pass_rate": None, "under_test": {"kind": kind, "name": name},
    }
    used = used if used is not None else [None] * len(outcomes)
    samples = [
        SimpleNamespace(
            id=scenario, error=None, metadata={"whitney_trigger": trigger} if trigger else {},
            scores={"s": SimpleNamespace(answer=outcome, metadata={} if was is None else {"used": None if was == "?" else was})},
        )
        for outcome, was in zip(outcomes, used, strict=True)
    ]
    return SimpleNamespace(eval=SimpleNamespace(task="x", metadata=metadata), samples=samples)


def skill_logs(**changes):
    logs = {
        "behaviour-baseline": log("baseline", "baseline", "writes-notes", ["fail"] * 3),
        "behaviour-template": log("template", "template", "writes-notes", ["pass"] * 3, used=[True, True, False]),
        "positive-template": log("template", "template", "asks-for-release-notes", ["pass"] * 3, trigger="positive"),
        "positive-control": log("trigger_control", "control", "asks-for-release-notes", ["fail"] * 3, trigger="positive"),
        "negative-template": log("template", "template", "asks-for-a-bug-fix", ["pass", "pass", "fail"], trigger="negative"),
    } | changes
    return [entry for entry in logs.values() if entry is not None]


def rendered(logs):
    from whitney.report import records_from_log, render_markdown, summarise

    return render_markdown(summarise([record for entry in logs for record in records_from_log(entry)]))


def test_a_file_with_trigger_tests_gets_a_table_for_each_kind():
    assert rendered(skill_logs()) == """## Release notes skill: pass / (pass + fail)

| Scenario | baseline | template | Δ |
|---|---|---|---|
| writes-notes | 0/3 | 3/3 | +100% |
| **overall** | 0/3 | 3/3 | +100% |

In the template's 3 scenario runs, the skill `release-notes` was loaded in 2, wasn't in 1 and couldn't be told in 0.

### Triggered on positive prompts

| Prompt | template | trigger_control | Δ |
|---|---|---|---|
| asks-for-release-notes | 3/3 | 0/3 | +100% |
| **overall** | 3/3 | 0/3 | +100% |

### Stayed quiet on negative prompts

| Prompt | template |
|---|---|
| asks-for-a-bug-fix | 2/3 |
| **overall** | 2/3 |

The gate reads the template's scenarios and its trigger tests separately: scenarios 3/3, trigger tests 5/6.
"""


def gate_problems(logs, **options):
    from whitney.report import check_thresholds, records_from_log, summarise

    return check_thresholds(summarise([record for entry in logs for record in records_from_log(entry)]), **options)


def test_the_gate_reads_scenarios_and_trigger_tests_separately():
    failing_triggers = skill_logs(**{"negative-template": log("template", "template", "asks-for-a-bug-fix", ["fail"] * 3, trigger="negative")})

    assert gate_problems(skill_logs()) == []
    assert gate_problems(failing_triggers) == ["release_notes: template trigger tests 50% is below the minimum 80%"]


def test_trigger_tests_that_all_pass_dont_carry_scenarios_that_fail():
    # 3 of 6 scenario runs and 6 of 6 trigger tests: 75% together, and with more trigger tests it would pass
    half_followed = skill_logs(**{
        "behaviour-template": log("template", "template", "writes-notes", ["pass"] * 3 + ["fail"] * 3),
        "positive-template": log("template", "template", "asks-for-release-notes", ["pass"] * 20, trigger="positive"),
        "negative-template": log("template", "template", "asks-for-a-bug-fix", ["pass"] * 20, trigger="negative"),
    })

    assert gate_problems(half_followed) == ["release_notes: template scenarios 50% is below the minimum 80%"]


def test_both_parts_falling_short_are_both_named():
    failing = skill_logs(**{
        "behaviour-template": log("template", "template", "writes-notes", ["fail"] * 3),
        "positive-template": log("template", "template", "asks-for-release-notes", ["fail"] * 3, trigger="positive"),
    })

    assert gate_problems(failing) == [
        "release_notes: template scenarios 0% is below the minimum 80%",
        "release_notes: template trigger tests 33% is below the minimum 80%",
    ]


def test_a_file_with_trigger_tests_and_no_scenarios_is_gated_on_the_trigger_tests():
    only_triggers = skill_logs(**{"behaviour-baseline": None, "behaviour-template": None})

    assert gate_problems(only_triggers) == []


def test_each_part_needs_enough_conclusive_runs_of_its_own():
    # 1 of 4 scenario runs was conclusive; with the trigger tests counted in, 7 of 10 would be
    early = skill_logs(**{"behaviour-template": log("template", "template", "writes-notes", ["pass"] + ["inconclusive"] * 3)})
    quiet = skill_logs(**{
        "positive-template": log("template", "template", "asks-for-release-notes", ["inconclusive"] * 3, trigger="positive"),
        "negative-template": log("template", "template", "asks-for-a-bug-fix", ["pass"] + ["inconclusive"] * 2, trigger="negative"),
    })
    silent = skill_logs(**{"behaviour-template": log("template", "template", "writes-notes", ["inconclusive"] * 3)})

    assert gate_problems(early) == ["release_notes: only 1/4 template scenario runs were conclusive, below the minimum 50%"]
    assert gate_problems(quiet) == ["release_notes: only 1/6 template trigger test runs were conclusive, below the minimum 50%"]
    assert gate_problems(silent) == ["release_notes: template scenarios had no conclusive runs, so they are below the minimum 80%"]


def marked_known_gap(entry):
    for sample in entry.samples:
        sample.metadata["known_gap"] = "E1: why"
    return entry


def test_a_gap_marked_in_one_log_doesnt_take_another_logs_failing_runs_out_of_the_gate():
    # two runs' logs in one folder, the scenario a known gap in the older one only
    logs = skill_logs(**{
        "behaviour-baseline": marked_known_gap(log("baseline", "baseline", "writes-notes", ["fail"] * 3)),
        "behaviour-template": log("template", "template", "writes-notes", ["fail"] * 3),
        "other-template": log("template", "template", "other", ["pass"] * 3),
    })

    assert gate_problems(logs) == ["release_notes: template scenarios 50% is below the minimum 80%"]


def test_a_run_counts_in_the_part_its_own_log_puts_it_in():
    # a stale log marks one run of a scenario as a trigger test: the scenario's failing runs still count as scenarios
    stale = log("trigger_control", "control", "writes-notes", ["fail"], trigger="positive")
    logs = skill_logs(**{"behaviour-template": log("template", "template", "writes-notes", ["fail"] * 3), "stale": stale})

    assert gate_problems(logs) == ["release_notes: template scenarios 0% is below the minimum 80%"]


def test_a_file_whose_scenarios_are_all_known_gaps_fails_as_one_without_trigger_tests_does():
    logs = skill_logs(**{
        "behaviour-baseline": None,
        "behaviour-template": marked_known_gap(log("template", "template", "writes-notes", ["fail"] * 3)),
    })

    assert gate_problems(logs) == ["release_notes: template scenarios had no conclusive runs, so they are below the minimum 80%"]
    assert "separately: scenarios no runs, trigger tests 5/6." in rendered(logs)


def test_trigger_tests_that_ran_only_in_the_control_fail_the_gate():
    logs = skill_logs(**{"positive-template": None, "negative-template": None})

    assert gate_problems(logs) == ["release_notes: template trigger tests had no conclusive runs, so they are below the minimum 80%"]


def test_a_file_with_only_trigger_tests_names_only_them_under_its_tables():
    text = rendered(skill_logs(**{"behaviour-baseline": None, "behaviour-template": None}))

    assert text.rstrip().endswith("The gate reads the template's trigger tests: 5/6.")


def test_an_agents_note_says_used_and_counts_runs_that_couldnt_be_told():
    logs = [log("template", "template", "writes-notes", ["pass"] * 4, used=[True, False, "?", "?"], kind="agent", name="reviewer")]

    assert (
        "In the template's 4 scenario runs, the agent `reviewer` was used in 1, wasn't in 1 and couldn't be told in 2."
        in rendered(logs)
    )


def test_only_the_templates_scenario_runs_are_counted_in_the_note():
    logs = skill_logs(**{
        "behaviour-baseline": log("baseline", "baseline", "writes-notes", ["fail"] * 3, used=[False] * 3),
        "positive-template": log("template", "template", "asks-for-release-notes", ["pass"] * 3, trigger="positive", used=[True] * 3),
    })

    assert "In the template's 3 scenario runs, the skill `release-notes` was loaded in 2, wasn't in 1" in rendered(logs)


def test_a_file_with_only_one_kind_of_prompt_gets_only_that_table():
    only_negative = rendered(skill_logs(**{"positive-template": None, "positive-control": None}))
    only_positive = rendered(skill_logs(**{"negative-template": None}))

    assert "### Triggered on positive prompts" not in only_negative
    assert "### Stayed quiet on negative prompts" in only_negative
    assert "### Triggered on positive prompts" in only_positive
    assert "### Stayed quiet on negative prompts" not in only_positive


def test_a_control_whose_positive_prompts_never_failed_is_named():
    logs = skill_logs(**{
        "positive-control": log("trigger_control", "control", "asks-for-release-notes", ["pass"] * 3, trigger="positive"),
    })

    assert "`trigger_control` never failed in 3 conclusive runs, so the template's result may not show the rule holding." in rendered(logs)


def test_a_file_without_trigger_tests_renders_as_before_with_its_note():
    logs = [
        log("baseline", "baseline", "writes-notes", ["fail"] * 2),
        log("template", "template", "writes-notes", ["pass"] * 2, used=[True, True]),
    ]

    assert rendered(logs) == """## Release notes skill: pass / (pass + fail)

| Scenario | baseline | template | Δ |
|---|---|---|---|
| writes-notes | 0/2 | 2/2 | +100% |
| **overall** | 0/2 | 2/2 | +100% |

In the template's 2 scenario runs, the skill `release-notes` was loaded in 2, wasn't in 0 and couldn't be told in 0.
"""


def test_runs_whose_scores_dont_say_are_counted_as_couldnt_be_told():
    logs = [log("template", "template", "writes-notes", ["pass"] * 2)]

    assert "In the template's 2 scenario runs, the skill `release-notes` was loaded in 0, wasn't in 0 and couldn't be told in 2." in rendered(logs)


def test_a_file_that_tests_no_skill_or_agent_gets_no_note():
    plain = log("template", "template", "writes-notes", ["pass"] * 2, used=[True, True])
    plain.eval.metadata["under_test"] = None

    assert "scenario run" not in rendered([plain])


@pytest.mark.parametrize("recorded", ["skill", {"kind": "rule", "name": "x"}, {"kind": "skill"}, {"kind": "skill", "name": ""}, {"name": "x"}])
def test_a_log_that_names_what_it_tests_in_a_way_the_report_doesnt_know_gets_no_note(recorded):
    odd = log("template", "template", "writes-notes", ["pass"] * 2, used=[True, True])
    odd.eval.metadata["under_test"] = recorded

    assert "scenario run" not in rendered([odd])


def test_an_errored_run_and_a_malformed_score_are_counted_as_couldnt_be_told():
    entry = log("template", "template", "writes-notes", ["pass"] * 4, used=[True, True, True, True])
    entry.samples[0].error = "boom"
    entry.samples[1].scores = {}
    entry.samples[2].scores["s"].metadata = {"used": ["x"]}

    text = rendered([entry])

    assert "| writes-notes | 2/2 (2 inconclusive) |" in text
    assert "In the template's 4 scenario runs, the skill `release-notes` was loaded in 1, wasn't in 0 and couldn't be told in 3." in text


def test_one_run_is_a_run():
    assert "In the template's 1 scenario run, the skill" in rendered([log("template", "template", "writes-notes", ["pass"], used=[True])])


def test_a_file_run_in_one_variant_that_isnt_the_template_isnt_said_to_have_run_the_template_only():
    alone = rendered([log("baseline", "baseline", "writes-notes", ["fail"] * 2)])
    template = rendered([log("template", "template", "writes-notes", ["pass"] * 2, used=[True, True])])

    assert "Only `template` ran" not in alone
    assert "Only `template` ran for this file, so there is no Δ." in template


def test_sample_metadata_named_trigger_isnt_the_mark_in_a_report():
    """A log whose samples hold their author's own `trigger` note renders as a file without trigger tests."""
    entry = log("template", "template", "writes-notes", ["pass"] * 2, used=[True, True])
    for sample in entry.samples:
        sample.metadata["trigger"] = "positive"

    text = rendered([entry])

    assert "### Triggered" not in text
    assert "The gate reads" not in text


def test_a_mark_the_report_doesnt_know_leaves_a_scenario_a_scenario():
    entry = log("template", "template", "writes-notes", ["pass"] * 2, used=[True, True])
    for sample in entry.samples:
        sample.metadata["whitney_trigger"] = "neutral"

    assert "| writes-notes | 2/2 |" in rendered([entry]).split("###")[0]


def test_a_known_gap_among_the_scenarios_stays_out_of_their_overall_row():
    gap = log("template", "template", "known-hard", ["fail"] * 3)
    for sample in gap.samples:
        sample.metadata["known_gap"] = "E1: why"

    text = rendered([*skill_logs(), gap])

    assert "| known-hard (known gap) | — | 0/3 | — |" in text
    assert "| **overall** | 0/3 | 3/3 | +100% |" in text
    assert "The gate reads the template's scenarios and its trigger tests separately: scenarios 3/3, trigger tests 5/6." in text
    assert gate_problems([*skill_logs(), gap]) == []


# --- The totals under a trigger file's tables -------------------------------------------------


def test_the_overall_row_adds_every_row_but_the_known_gaps():
    from whitney.report import Cell, _overall

    rows = {
        "a-gap": {"template": Cell(passed=9, failed=9, inconclusive=9)},
        "b": {"template": Cell(passed=1, failed=2, inconclusive=3), "baseline": Cell(failed=1)},
        "c": {"template": Cell(passed=10, failed=20, inconclusive=30)},
    }

    assert _overall(rows, {"a-gap": "E3: why"}) == {"template": Cell(passed=11, failed=22, inconclusive=33), "baseline": Cell(failed=1)}


def test_the_gate_note_says_no_runs_only_when_a_part_has_none():
    from whitney.report import Cell, _gate_note

    def note(scenarios):
        return _gate_note([("scenario", scenarios), ("trigger test", Cell())], "unclear")

    start = "The gate reads the template's scenarios and its trigger tests separately: scenarios "
    assert note(Cell(passed=1, failed=1)) == start + "1/2, trigger tests no runs."
    assert note(Cell(failed=1, inconclusive=1)) == start + "0/1 (1 unclear), trigger tests no runs."
    assert note(Cell(inconclusive=2)) == start + "0/0 (2 unclear), trigger tests no runs."
