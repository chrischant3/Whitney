"""The `trigger` block on the claude-code target (Phase 5 Task 36).

A Claude Code project keeps its skills in .claude/skills/ and its agents in
.claude/agents/. The same block, checks and control work there as on Kilo;
the streams are the ones Claude Code 2.1.322 printed
(tests/data/claude-code-events/).
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from whitney import cli, tasks
from whitney.checks import Evidence, registry
from whitney.evalfile import load
from whitney.validate import staged_names, validate_evals, validate_project
from whitney.variants import TRIGGER_CONTROL, declared_variants

EVENTS = Path(__file__).parent / "data" / "claude-code-events"
MODEL = "anthropic/claude-haiku-4-5"
SKILL = ".claude/skills/release-notes/SKILL.md"
AGENT = ".claude/agents/q7-helper.md"
POSITIVE = [{"id": "asks-for-release-notes", "prompt": "Write the release notes for this version."}]
NEGATIVE = [
    {"id": "asks-for-a-bug-fix", "prompt": "Fix the failing test in tests/test_app.py.", "owner": "debugging"},
    {"id": "asks-the-time", "prompt": "What time is it?"},
]


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use for {name}.\n---\n\n# {name}\n"


def agent_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use for {name}.\n---\n\nDo the work.\n"


@pytest.fixture
def project(tmp_path):
    """A Claude Code project with two skills and two agents, and its evals root with one fixture."""
    root = tmp_path / "my-config"
    files = {
        "CLAUDE.md": "- Never commit .env files or secrets\n",
        SKILL: skill_md("release-notes"),
        ".claude/skills/release-notes/template.md": "# Release notes\n",
        ".claude/skills/debugging/SKILL.md": skill_md("debugging"),
        AGENT: agent_md("q7-helper"),
        ".claude/agents/planner.md": agent_md("planner"),
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


def agent_file(**overrides):
    negative = [{"id": "asks-for-a-plan", "prompt": "Plan the work.", "owner": "planner"}]
    return eval_file(AGENT, trigger={"positive": [{"id": "asks-for-a-count", "prompt": "How long is app.py?"}], "negative": negative}) | overrides


def write(project, data, name="skills/release-notes.json"):
    path = project / "evals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def task_for(project, variant, name="skills/release-notes.json"):
    return tasks.scenario_eval(
        name, variant, target="claude-code", model=MODEL, evals_path=str(project / "evals"), project_path=str(project),
    )


def staged(task) -> dict[str, str]:
    return task.metadata["evaluation_assets"]


def project_problems(project, target="claude-code"):
    result = validate_evals(project / "evals")
    assert result.problems == []
    return [(p.location, p.message) for p in validate_project(result.files, project, target, evals_path=project / "evals")]


# --- The eval files apply, validate and expand ------------------------------------------------


def test_a_claude_code_skills_file_validates_and_applies_to_claude_code(project):
    parsed = load(write(project, eval_file()))

    assert project_problems(project) == []
    assert cli.why_not_run(parsed, "claude-code", registry()) is None


def test_a_claude_code_agents_file_validates_and_applies_to_claude_code_alone(project):
    parsed = load(write(project, agent_file(), "agents/q7-helper.json"))

    assert project_problems(project) == []
    assert cli.why_not_run(parsed, "claude-code", registry()) is None
    assert cli.why_not_run(parsed, "kilo", registry()) == "it applies to claude-code"


def test_the_trigger_prompts_expect_the_same_checks_as_on_kilo(project):
    scenarios = {s.id: s for s in load(write(project, eval_file())).resolved()}

    assert [(e.check, e.params) for e in scenarios["asks-for-release-notes"].expect] == [("skill_loaded", {"skill": "release-notes"})]
    assert [(e.check, e.params) for e in scenarios["asks-for-a-bug-fix"].expect] == [
        ("skill_not_loaded", {"skill": "release-notes"}), ("skill_loaded", {"skill": "debugging"}),
    ]


def test_the_staged_skills_and_agents_are_named_for_claude_code(project):
    files = staged(task_for(project, "template", write(project, eval_file()).relative_to(project / "evals").as_posix()))

    assert list(staged_names(files, "skill")) == ["debugging", "release-notes"]
    assert staged_names(files, "agent") == {"planner": ".claude/agents/planner.md", "q7-helper": AGENT}


# --- The control removes the skill or the agent ----------------------------------------------


def test_the_control_removes_the_skills_whole_folder(project):
    path = write(project, eval_file())

    assert declared_variants(load(path))[TRIGGER_CONTROL].remove == [".claude/skills/release-notes/"]
    template, control = staged(task_for(project, "template")), staged(task_for(project, TRIGGER_CONTROL))
    assert set(template) - set(control) == {SKILL, ".claude/skills/release-notes/template.md"}
    assert [sample.id for sample in task_for(project, TRIGGER_CONTROL).dataset] == ["asks-for-release-notes"]


def test_the_control_removes_the_agents_file(project):
    write(project, agent_file(), "agents/q7-helper.json")

    template = staged(task_for(project, "template", "agents/q7-helper.json"))
    control = staged(task_for(project, TRIGGER_CONTROL, "agents/q7-helper.json"))

    assert set(template) - set(control) == {AGENT}


def test_the_baseline_of_a_skills_file_removes_the_skill_and_keeps_the_instructions(project):
    write(project, eval_file())

    baseline = staged(task_for(project, "baseline"))

    assert "CLAUDE.md" in baseline and SKILL not in baseline
    assert [sample.id for sample in task_for(project, "baseline").dataset] == ["writes-notes"]


# --- Validation against the project ----------------------------------------------------------


def test_an_owner_must_be_a_staged_skill_or_agent(project):
    trigger = {"negative": [{"id": "a", "prompt": "p", "owner": "refactoring"}]}
    write(project, eval_file(trigger=trigger))
    write(project, agent_file(trigger={"negative": [{"id": "a", "prompt": "p", "owner": "release-notes"}]}), "agents/q7-helper.json")

    assert project_problems(project) == [
        ("trigger.negative[0].owner", "no staged agent is named 'release-notes'; staged agents: planner, q7-helper"),
        ("trigger.negative[0].owner", "no staged skill is named 'refactoring'; staged skills: debugging, release-notes"),
    ]


def test_a_kilo_skills_file_is_not_checked_against_a_claude_code_project(project):
    """Its source keeps it to Kilo, so `whitney validate` for claude-code leaves it out, as `whitney run` does."""
    parsed = load(write(project, eval_file(".kilo/skills/release-notes/SKILL.md")))

    assert cli.why_not_run(parsed, "claude-code", registry()) == "it applies to kilo"
    assert project_problems(project) == []


def test_a_kilo_skills_file_that_names_claude_code_is_told_its_source_is_not_staged(project):
    write(project, eval_file(".kilo/skills/release-notes/SKILL.md", targets=["claude-code"], variants={}))

    [(at, message), *_] = project_problems(project)

    assert (at, message) == ("source", "'.kilo/skills/release-notes/SKILL.md' isn't staged: the project has no such file")


@pytest.mark.parametrize("frontmatter, said", [
    ("name: counter\ndescription: Counts.", "its frontmatter names it 'counter'"),
    ("description: Counts.", "its frontmatter has no name"),
    ("name: Q7-Helper\ndescription: Counts.", "its frontmatter names it 'Q7-Helper'"),
])
def test_a_claude_code_agent_under_test_must_be_named_as_its_file(project, frontmatter, said):
    """Claude Code 2.1.322 offers an agent under its frontmatter's name, and none without one; the checks look for the file's."""
    (project / AGENT).write_text(f"---\n{frontmatter}\n---\n\nCount.\n")
    write(project, agent_file(), "agents/q7-helper.json")

    assert project_problems(project) == [("source", (
        f"Claude Code calls an agent by the name in its frontmatter, and {said}; Whitney looks for 'q7-helper', "
        f"the file's name, so set `name: q7-helper` in {AGENT}"
    ))]


def test_a_claude_code_owner_agent_must_be_named_as_its_file_too(project):
    (project / ".claude/agents/planner.md").write_text("---\nname: architect\ndescription: Plans.\n---\n\nPlan.\n")
    write(project, agent_file(), "agents/q7-helper.json")

    assert project_problems(project) == [("trigger.negative[0].owner", (
        "Claude Code calls an agent by the name in its frontmatter, and its frontmatter names it 'architect'; "
        "Whitney looks for 'planner', the file's name, so set `name: planner` in .claude/agents/planner.md"
    ))]


def test_a_skills_frontmatter_name_is_not_held_to_its_folder(project):
    """Claude Code 2.1.322 offers a skill under its folder's name, whatever its frontmatter says."""
    (project / SKILL).write_text(skill_md("notes"))
    write(project, eval_file())

    assert project_problems(project) == []


def test_a_kilo_agents_name_is_not_checked(tmp_path):
    """Kilo names an agent by its file, and its agents have no `name` in their frontmatter."""
    root = tmp_path / "kilo-config"
    files = {
        "kilo.jsonc": "{}",
        ".kilo/agents/reviewer.md": "---\ndescription: Reviews.\nmode: subagent\n---\nReview.\n",
        "evals/fixtures/repo/project/README.md": "# App\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    write(root, eval_file(".kilo/agents/reviewer.md", trigger={"positive": POSITIVE}), "agents/reviewer.json")

    assert project_problems(root, "kilo") == []


# --- Whether the skill or agent under test was used ------------------------------------------


def _used_by(task, run, sample_id, monkeypatch):
    """`used` in the metadata of the task's score for one sample, given the run the agent stored."""

    async def collect(*args, **kwargs):
        return Evidence(exit_code=0)

    monkeypatch.setattr(tasks, "store", lambda: SimpleNamespace(get=lambda key, default=None: run))
    monkeypatch.setattr(tasks.evidence, "collect", collect)
    monkeypatch.setattr(tasks, "sandbox", lambda: None)
    [score] = task.scorer
    state = SimpleNamespace(sample_id=sample_id, uuid=f"none-{sample_id}", metadata={})
    return asyncio.run(score(state, None)).metadata["used"]


@pytest.mark.parametrize("case, used", [("skill-named", True), ("subagent-loads-skill", True), ("skill-not-loaded", False)])
def test_the_tasks_scorer_says_whether_the_skill_under_test_was_used_on_claude_code(project, monkeypatch, case, used):
    write(project, eval_file())
    run = {"events": (EVENTS / f"{case}.jsonl").read_text(), "exit_code": 0, "stderr": ""}

    assert _used_by(task_for(project, "template"), run, "writes-notes", monkeypatch) is used


def test_the_tasks_scorer_says_whether_the_agent_under_test_was_used_on_claude_code(project, monkeypatch):
    write(project, agent_file(), "agents/q7-helper.json")
    run = {"events": (EVENTS / "subagent-description.jsonl").read_text(), "exit_code": 0, "stderr": ""}

    assert _used_by(task_for(project, "template", "agents/q7-helper.json"), run, "writes-notes", monkeypatch) is True
