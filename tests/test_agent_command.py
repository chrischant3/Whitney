"""The `agent` and `command` scenario fields: `kilo run --agent NAME` and `kilo run --command NAME`.

The forms are the ones plan Task 16 captured on Kilo 7.8.18
(docs/spike-findings.md, "Case 4"): the option goes before the `--` that ends
the options, a missing or `mode: subagent` agent makes Kilo fall back to its
default agent and exit 0, and a missing command makes it exit 1 before any
model call.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser

from whitney import agent as agent_module
from whitney import targets, tasks
from whitney.checks import Verdict, registry
from whitney.evalfile import EvalFileError, load
from whitney.validate import validate_evals, validate_project

MODEL = "test-provider/test-model"
EVENTS = Path(__file__).parent / "data" / "kilo-events"
PRIMARY = "---\ndescription: Reviews code.\nmode: primary\n---\nNever edit files.\n"
SUBAGENT = "---\ndescription: Counts lines.\nmode: subagent\n---\nCount the lines.\n"


@pytest.fixture
def project(tmp_path):
    """A consumer project with a primary agent, a subagent and a command, and its evals root with one fixture."""
    root = tmp_path / "my-config"
    files = {
        "AGENTS.md": "- Never commit .env files or secrets\n",
        "kilo.jsonc": "{}",
        ".kilo/agents/reviewer.md": PRIMARY,
        ".kilo/agents/counter.md": SUBAGENT,
        ".kilo/agents/unsaid.md": "---\ndescription: No mode.\n---\nHelp.\n",
        ".kilo/commands/shout.md": "Reply in capitals: $ARGUMENTS\n",
        "evals/fixtures/repo/project/app.py": "print('hi')\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def eval_file(*scenarios, source="AGENTS.md", **overrides):
    return {
        "version": 2,
        "source": source,
        "targets": ["kilo"],
        "defaults": {"fixture": "repo", "expect": [{"check": "no_file_edits"}]},
        "scenarios": list(scenarios) or [{"id": "fix-bug", "prompt": "Fix the bug.", "agent": "reviewer"}],
    } | overrides


def write(project, data, name="agents-md.json"):
    path = project / "evals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def project_problems(project):
    result = validate_evals(project / "evals")
    assert result.problems == []
    return [(p.location, p.message) for p in validate_project(result.files, project, "kilo", evals_path=project / "evals")]


# --- The schema ------------------------------------------------------------------------------


def test_a_scenario_can_name_an_agent_or_a_command(project):
    path = write(project, eval_file(
        {"id": "a", "prompt": "p", "agent": "reviewer"}, {"id": "b", "prompt": "p", "command": "shout"}, {"id": "c", "prompt": "p"},
    ))

    assert [(s.id, s.agent, s.command) for s in load(path).resolved()] == [("a", "reviewer", None), ("b", None, "shout"), ("c", None, None)]


@pytest.mark.parametrize("field", ["agent", "command"])
@pytest.mark.parametrize("name", ["", "--auto", "-x", "two words", "a/b", "../x", "a\nb", 7])
def test_a_name_that_could_be_read_as_an_option_or_a_path_is_refused(project, field, name):
    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file({"id": "a", "prompt": "p", field: name})))

    assert [p.location for p in raised.value.problems] == [f"scenarios[0].{field}"]


def test_a_scenario_names_one_or_the_other(project):
    with pytest.raises(EvalFileError) as raised:
        load(write(project, eval_file({"id": "a", "prompt": "p", "agent": "reviewer", "command": "shout"})))

    assert [(p.location, p.message) for p in raised.value.problems] == [(
        "scenarios[0]", "Value error, set agent or command, not both: Kilo was only captured running each alone",
    )]


def test_neither_field_can_be_a_default(project):
    data = eval_file({"id": "a", "prompt": "p"}, defaults={"fixture": "repo", "agent": "reviewer", "expect": [{"check": "no_file_edits"}]})

    with pytest.raises(EvalFileError) as raised:
        load(write(project, data))

    assert [p.location for p in raised.value.problems] == ["defaults.agent"]


# --- The command line ------------------------------------------------------------------------


def state(prompt="Fix the bug."):
    return AgentState(messages=[ChatMessageUser(content="earlier"), ChatMessageUser(content=prompt)])


def test_each_field_adds_exactly_the_captured_option_before_the_prompt():
    start = ["kilo", "run", "--pure", "--format", "json", "-m", MODEL]

    assert agent_module._kilo_command(state(), MODEL, False) == [*start, "--", "Fix the bug."]
    assert agent_module._kilo_command(state(), MODEL, False, agent_name="reviewer") == [*start, "--agent", "reviewer", "--", "Fix the bug."]
    assert agent_module._kilo_command(state(), MODEL, False, command_name="shout") == [*start, "--command", "shout", "--", "Fix the bug."]
    assert agent_module._kilo_command(state(), MODEL, True, agent_name="reviewer") == [
        *start, "--auto", "--agent", "reviewer", "--", "Fix the bug.",
    ]


@pytest.mark.parametrize("options", [{"agent_name": "reviewer"}, {"command_name": "shout"}])
def test_neither_field_ever_adds_auto(options):
    assert "--auto" not in agent_module._kilo_command(state("--auto"), MODEL, False, **options)[:-1]


@pytest.mark.parametrize("options, expected", [
    ({"agent_name": "reviewer"}, ["--agent", "reviewer"]),
    ({"command_name": "shout"}, ["--command", "shout"]),
    ({}, []),
])
def test_a_local_run_passes_the_option_to_kilo(tmp_path, monkeypatch, options, expected):
    """End to end against a fake `kilo` on PATH that records its arguments."""
    bin_dir, sandbox_project = tmp_path / "bin", tmp_path / "sandbox-project"
    bin_dir.mkdir()
    sandbox_project.mkdir()
    (bin_dir / "kilo").write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > "$PWD/args.txt"\necho \'{"type":"text","part":{"text":"done"}}\'\n')
    (bin_dir / "kilo").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    async def exec_(command, **kwargs):
        return SimpleNamespace(stdout=f"{sandbox_project}\n", returncode=0, stderr="")

    monkeypatch.setattr(agent_module, "sandbox", lambda: SimpleNamespace(exec=exec_))
    monkeypatch.setattr(agent_module, "store", lambda: SimpleNamespace(set=lambda *a: None))

    asyncio.run(agent_module.kilo(model=MODEL, timeout=30, **options)(state()))

    assert (sandbox_project / "args.txt").read_text().splitlines() == [
        "run", "--pure", "--format", "json", "-m", MODEL, *expected, "--", "Fix the bug.",
    ]


def test_build_agent_passes_the_names_to_kilo_only_when_they_are_given(monkeypatch):
    built = []
    monkeypatch.setattr(targets, "kilo", lambda **kwargs: built.append(kwargs))

    targets.build_agent("kilo", MODEL, {"kilo": {}})
    targets.build_agent("kilo", MODEL, {"kilo": {}}, kilo_agent="reviewer")
    targets.build_agent("kilo", MODEL, {"kilo": {}}, kilo_command="shout")

    assert [{k: v for k, v in kwargs.items() if k in ("agent_name", "command_name")} for kwargs in built] == [
        {}, {"agent_name": "reviewer"}, {"command_name": "shout"},
    ]


@pytest.mark.parametrize("option", ["kilo_agent", "kilo_command"])
def test_claude_code_takes_neither(option):
    with pytest.raises(ValueError, match="only the kilo target runs a named agent or command"):
        targets.build_agent("claude-code", "anthropic/claude-haiku-4-5", {"claude-code": {}}, **{option: "x"})


# --- The tasks -------------------------------------------------------------------------------


def task_for(project, variant="template", name="agents-md.json"):
    return tasks.scenario_eval(name, variant, target="kilo", model=MODEL, evals_path=str(project / "evals"), project_path=str(project))


def options(task):
    return {sample.id: sample.metadata.get("kilo_options") for sample in task.dataset}


def test_each_sample_records_the_option_its_scenario_runs_with(project):
    write(project, eval_file(
        {"id": "a", "prompt": "p", "agent": "reviewer"}, {"id": "b", "prompt": "p", "command": "shout"}, {"id": "c", "prompt": "p"},
    ))

    assert options(task_for(project)) == {"a": {"agent": "reviewer"}, "b": {"command": "shout"}, "c": None}


def test_a_variant_that_removes_the_agent_runs_the_same_prompt_with_kilos_default_agent(project):
    """Decision 6, as plan Task 16 changed it: the baseline drops --agent rather than relying on Kilo's fallback."""
    write(project, eval_file(source=".kilo/agents/reviewer.md"), "agents/reviewer.json")

    template, baseline = task_for(project, name="agents/reviewer.json"), task_for(project, "baseline", "agents/reviewer.json")

    assert options(template) == {"fix-bug": {"agent": "reviewer"}}
    assert options(baseline) == {"fix-bug": None}
    assert [sample.input for sample in baseline.dataset] == [sample.input for sample in template.dataset]
    assert ".kilo/agents/reviewer.md" not in baseline.metadata["evaluation_assets"]


def test_a_variant_that_keeps_the_agent_keeps_the_option(project):
    write(project, eval_file(variants={"no_rules": {"remove": ["AGENTS.md"]}}))

    assert options(task_for(project, "no_rules")) == {"fix-bug": {"agent": "reviewer"}}


def test_the_task_builds_one_agent_per_option_and_the_plain_one_first(project, monkeypatch):
    write(project, eval_file(
        {"id": "a", "prompt": "p", "agent": "reviewer"}, {"id": "b", "prompt": "p", "command": "shout"},
        {"id": "c", "prompt": "p"}, {"id": "d", "prompt": "p", "agent": "reviewer"},
    ))
    seen = []
    build_agent = targets.build_agent
    monkeypatch.setattr(targets, "build_agent", lambda *args, **kwargs: (seen.append(kwargs), build_agent(*args, **kwargs))[1])

    task_for(project)

    assert [{k: v for k, v in kwargs.items() if k.startswith("kilo_")} for kwargs in seen] == [
        {}, {"kilo_agent": "reviewer"}, {"kilo_command": "shout"},
    ]


def test_a_file_without_either_field_builds_its_one_agent_as_before(project, monkeypatch):
    write(project, eval_file({"id": "c", "prompt": "p"}))
    seen = []
    build_agent = targets.build_agent
    monkeypatch.setattr(targets, "build_agent", lambda *args, **kwargs: (seen.append(kwargs), build_agent(*args, **kwargs))[1])

    task_for(project)

    assert seen == [{"execution": "local", "api_key_env": None, "base_url": None}]


def test_each_sample_is_run_by_the_agent_built_for_its_option():
    ran = []

    def fake(label):
        async def solve(state, generate):
            ran.append((state.sample_id, label))
            return state

        return solve

    solvers = {None: fake("plain"), ("agent", "reviewer"): fake("reviewer"), ("command", "shout"): fake("shout")}
    solve = tasks._by_option(solvers, {"a": {"agent": "reviewer"}, "b": {"command": "shout"}})
    for sample_id, metadata in [("a", {}), ("b", {}), ("c", {}), ("d", {"kilo_options": {"agent": "reviewer"}}), (7, {})]:
        asyncio.run(solve(SimpleNamespace(sample_id=sample_id, metadata=metadata), None))

    # d's metadata names an option, but only the eval file's own fields choose one.
    assert ran == [("a", "reviewer"), ("b", "shout"), ("c", "plain"), ("d", "plain"), (7, "plain")]


@pytest.mark.parametrize("written", [{"agent": "--auto"}, {"command": "--auto"}, {"mode": "x"}, "--auto", ["--auto"]])
def test_a_scenarios_own_metadata_never_reaches_kilos_command_line(project, monkeypatch, written):
    """Scenario metadata is free-form, so nothing read from it may choose an option: only `agent` and `command` do."""
    write(project, eval_file(
        {"id": "a", "prompt": "p", "metadata": {"kilo_options": written, "route": "x"}},
        {"id": "b", "prompt": "p", "agent": "reviewer", "metadata": {"kilo_options": written}},
    ))
    seen = []
    build_agent = targets.build_agent
    monkeypatch.setattr(targets, "build_agent", lambda *args, **kwargs: (seen.append(kwargs), build_agent(*args, **kwargs))[1])

    task = task_for(project)

    assert [{k: v for k, v in kwargs.items() if k.startswith("kilo_")} for kwargs in seen] == [{}, {"kilo_agent": "reviewer"}]
    assert options(task) == {"a": None, "b": {"agent": "reviewer"}}
    assert [sample.metadata.get("route") for sample in task.dataset] == ["x", None]


def test_an_agent_that_was_never_staged_keeps_its_option_so_the_fallback_rule_can_see_it(project):
    """Reachable only by building the task directly: `whitney run` validates first and refuses the name."""
    write(project, eval_file({"id": "a", "prompt": "p", "agent": "reviewr"}))

    assert options(task_for(project)) == {"a": {"agent": "reviewr"}}
    assert options(task_for(project, "baseline")) == {"a": {"agent": "reviewr"}}


def test_the_scorer_applies_the_fallback_rule_to_a_scenario_that_names_an_agent(project, tmp_path, monkeypatch):
    write(project, eval_file({"id": "a", "prompt": "p", "agent": "reviewer"}, {"id": "b", "prompt": "p"}))
    run = {"exit_code": 0, "events": "", "stderr": FALLBACK}
    monkeypatch.setattr(tasks, "store", lambda: SimpleNamespace(get=lambda key, default=None: run))

    async def collect(*args, **kwargs):
        from whitney.checks import Evidence

        return Evidence(exit_code=0)

    monkeypatch.setattr(tasks.evidence, "collect", collect)
    monkeypatch.setattr(tasks, "sandbox", lambda: None)
    [score] = task_for(project).scorer

    def explanation(sample_id):
        state = SimpleNamespace(sample_id=sample_id, uuid=f"none-{sample_id}", metadata={"kilo_options": {"agent": "x"}})
        return asyncio.run(score(state, None)).explanation

    assert "Kilo fell back to its default agent, so the agent reviewer didn't run" in explanation("a")
    assert "fell back" not in explanation("b")


def test_docker_mode_passes_the_option_to_kilo_too():
    seen = []

    class Sandbox:
        async def exec(self, command, **kwargs):
            seen.append(command)
            return SimpleNamespace(stdout="/workspace/project\n", returncode=0, stderr="")

    asyncio.run(agent_module._run_kilo(Sandbox(), state(), MODEL, False, {"HOME": "/root"}, 30, named={"agent_name": "reviewer"}))
    asyncio.run(agent_module._run_kilo(Sandbox(), state(), MODEL, False, {"HOME": "/root"}, 30))

    kilo_runs = [command for command in seen if command[0] == "kilo"]
    assert [("--agent" in command, command[-2:]) for command in kilo_runs] == [(True, ["--", "Fix the bug."]), (False, ["--", "Fix the bug."])]
    assert kilo_runs[0][kilo_runs[0].index("--agent") + 1] == "reviewer"


# --- A run where Kilo fell back to its default agent ------------------------------------------

FALLBACK = '\x1b[93m\x1b[1m! \x1b[0m agent "reviewer" is a subagent, not a primary agent. Falling back to default agent\n'


def verdicts():
    checks = registry()
    return [
        (checks["no_file_edits"], Verdict("pass", "no watched file was added")),
        (checks["no_string_added"], Verdict("fail", "a watched string's count rose", details={"seen": ["x"]})),
    ]


def test_a_run_that_fell_back_to_the_default_agent_judges_nothing_about_the_named_one():
    judged = tasks._without_the_named_agent(verdicts(), {"agent": "reviewer"}, {"stderr": FALLBACK})

    assert [(v.outcome, v.details) for _, v in judged] == [("inconclusive", {}), ("inconclusive", {"seen": ["x"]})]
    assert judged[0][1].explanation == (
        "no watched file was added, but Kilo fell back to its default agent, so the agent reviewer didn't run"
    )


@pytest.mark.parametrize("chosen, stderr", [
    ({"agent": "reviewer"}, ""),
    ({"agent": "reviewer"}, "! permission requested: bash (git push); auto-rejecting\n"),
    (None, FALLBACK),
    ({}, FALLBACK),
    ({"command": "shout"}, FALLBACK),
])
def test_other_runs_are_judged_as_they_were(chosen, stderr):
    original = verdicts()

    assert tasks._without_the_named_agent(original, chosen, {"stderr": stderr}) == original


def test_the_captured_fallback_runs_are_recognised_and_the_primary_run_is_not():
    def fell_back(case):
        meta = json.loads((EVENTS / f"{case}.meta.json").read_text())
        judged = tasks._without_the_named_agent(verdicts(), {"agent": "x"}, {"stderr": meta["stderr"]})
        return judged[0][1].outcome == "inconclusive"

    assert [fell_back(case) for case in ("agent-option-subagent", "agent-option-missing", "agent-option-primary")] == [True, True, False]


def test_a_run_with_no_recorded_stderr_is_judged_as_it_was():
    original = verdicts()

    assert tasks._without_the_named_agent(original, {"agent": "reviewer"}, {}) == original


# --- Validation ------------------------------------------------------------------------------


def test_a_primary_agent_and_a_staged_command_are_valid(project):
    write(project, eval_file({"id": "a", "prompt": "p", "agent": "reviewer"}, {"id": "b", "prompt": "p", "command": "shout"}))

    assert project_problems(project) == []


def test_an_agent_must_be_staged(project):
    write(project, eval_file({"id": "a", "prompt": "p"}, {"id": "b", "prompt": "p", "agent": "auditor"}))

    assert project_problems(project) == [("scenarios[1].agent", "no staged agent is named 'auditor'; staged agents: counter, reviewer, unsaid")]


@pytest.mark.parametrize("name, said", [("counter", "its mode is 'subagent'"), ("unsaid", "it has no mode")])
def test_an_agent_must_be_a_primary_agent(project, name, said):
    write(project, eval_file({"id": "a", "prompt": "p", "agent": name}))

    assert project_problems(project) == [("scenarios[0].agent", (
        f"{said}, and `kilo run --agent` was only captured working with `mode: primary`; "
        "with a subagent Kilo falls back to its default agent"
    ))]


def test_a_command_must_be_staged(project):
    write(project, eval_file({"id": "a", "prompt": "p", "command": "whisper"}))

    assert project_problems(project) == [("scenarios[0].command", "no staged command is named 'whisper'; staged commands: shout")]


def test_a_command_can_be_a_workflow_file(project):
    (project / ".kilo" / "workflows").mkdir()
    (project / ".kilo" / "workflows" / "release.md").write_text("Release it.\n")
    write(project, eval_file({"id": "a", "prompt": "p", "command": "release"}))

    assert project_problems(project) == []


@pytest.mark.parametrize("field, name", [("agent", "reviewer"), ("command", "shout")])
@pytest.mark.parametrize("targets_given", [None, ["kilo", "claude-code"], ["claude-code"]])
def test_either_field_is_refused_in_a_file_that_applies_to_claude_code(project, field, name, targets_given):
    data = eval_file({"id": "a", "prompt": "p"}, {"id": "b", "prompt": "p", field: name})
    if targets_given is None:
        del data["targets"]
    else:
        data["targets"] = targets_given
    path = write(project, data)

    found = [(p.file, p.location, p.message) for p in validate_evals(project / "evals").problems]

    assert found == [(path, f"scenarios[1].{field}", (
        f"`kilo run --{field}` is Kilo's alone, and this file applies to claude-code too; set \"targets\": [\"kilo\"]"
    ))]


def test_a_file_whose_checks_keep_it_to_kilo_needs_no_targets(project):
    data = eval_file({"id": "a", "prompt": "p", "agent": "reviewer", "expect": [{"check": "skill_not_loaded", "skill": "x"}]})
    del data["targets"]
    write(project, data)

    assert validate_evals(project / "evals").problems == []


def test_a_variant_may_not_remove_a_command_a_scenario_runs(project):
    """A missing command makes `kilo run --command` exit 1 before any model call, so every such run would be inconclusive."""
    write(project, eval_file({"id": "a", "prompt": "p", "command": "shout"}, source=".kilo/commands/shout.md"), "commands/shout.json")

    assert project_problems(project) == [("source", (
        "the default baseline removes the source: it removes the command 'shout', which scenario 'a' runs; "
        "`kilo run --command` fails without it, so keep it in every variant (\"variants\": {} runs the template only)"
    ))]


def test_a_declared_variant_that_removes_the_command_is_named_where_it_is(project):
    write(project, eval_file({"id": "a", "prompt": "p", "command": "shout"}, variants={"bare": {"remove": [".kilo/commands/"]}}))

    assert [location for location, _ in project_problems(project)] == ["variants.bare"]


def test_a_variant_that_keeps_the_command_is_valid(project):
    write(project, eval_file({"id": "a", "prompt": "p", "command": "shout"}, variants={"no_rules": {"remove": ["AGENTS.md"]}}))

    assert project_problems(project) == []


# --- End to end: `whitney run` with a fake kilo that records how it was run --------------------


def test_whitney_run_gives_each_scenario_its_own_kilo_command(project, tmp_path):
    """The whole path through Inspect: the eval file's fields reach `kilo run`, sample by sample, and never `--auto`."""
    import subprocess
    import sys

    from conftest import blocking_git_hooks_home

    write(project, eval_file(
        {"id": "with-agent", "prompt": "Fix the bug.", "agent": "reviewer", "metadata": {"kilo_options": {"agent": "--auto"}}},
        {"id": "with-command", "prompt": "hello", "command": "shout"},
        {"id": "plain", "prompt": "Say hello."},
        variants={},
    ))
    record, bin_dir = tmp_path / "runs.txt", tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "kilo").write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then echo "7.8.18-fake"; exit 0; fi\n'
        f'echo "$*" >> {record}\n'
        'echo \'{"type":"text","part":{"text":"done"}}\'\n'
    )
    (bin_dir / "kilo").chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "no_proxy": "*"}
    env.update({name: value for name, value in blocking_git_hooks_home(tmp_path / "user-home").items() if value is not None})

    done = subprocess.run(
        [sys.executable, "-m", "whitney.cli", "run", "--target", "kilo", "--model", MODEL, "--project-path", str(project),
         "--evals-path", str(project / "evals"), "--epochs", "1", "--max-samples", "1"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=300,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    start = f"run --pure --format json -m {MODEL}"
    assert sorted(record.read_text().splitlines()) == sorted([
        f"{start} --agent reviewer -- Fix the bug.", f"{start} --command shout -- hello", f"{start} -- Say hello.",
    ])
