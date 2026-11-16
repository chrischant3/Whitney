"""Behaviour of whitney.refusals, whitney.validation and whitney.tasks that the 2026-11-15 full mutation run on main
found untested (Later plan Task 65).

Each test pins one thing a user would see: the words a refusal is reported in, a warning validate gives or
doesn't, or what a built task records. Streams are written by hand in the shapes the recorded ones have
(tests/data/claude-code-events, tests/data/kilo-events).
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from whitney import tasks, validation
from whitney.checks import Evidence
from whitney.refusals import Rule, refused
from whitney.validation import skill_copy_warnings, skill_description_warnings, validate_evals

BLOCKED = "Skill execution blocked by permission rules"
SKILL_RULE = Rule("skill", "release-notes", "Skill(release-notes)")
AGENT_RULE = Rule("agent", "reviewer", "Agent(reviewer)")
DENIED_AGENT = "Agent type 'reviewer' has been denied by permission rule 'Agent(reviewer)' from flagSettings."
MODEL = "test-provider/test-model"
CLAUDE_MODEL = "anthropic/claude-haiku-4-5"


def line(event: dict) -> str:
    return json.dumps(event)


def user(*blocks) -> str:
    return line({"type": "user", "message": {"content": list(blocks)}})


def assistant(*blocks) -> str:
    return line({"type": "assistant", "message": {"content": list(blocks)}})


def result(call: str, **fields) -> dict:
    return {"type": "tool_result", "tool_use_id": call, "is_error": True, **fields}


def skill_denial(*lines: str) -> dict:
    """A Claude Code run whose one Skill(release-notes) call was denied, with `lines` as its stream."""
    denial = {"tool_name": "Skill", "tool_input": {"skill": "release-notes"}, "tool_use_id": "s1"}
    return {"events": "\n".join(lines), "permission_denials": [denial]}


def agent_call(*lines: str) -> dict:
    """A Claude Code run with one Agent call, a1, and `lines` after it."""
    call = {"type": "tool_use", "id": "a1", "name": "Agent", "input": {"subagent_type": "reviewer"}}
    return {"events": "\n".join([assistant(call), *lines])}


# --- refusals: how a refusal's words are read -------------------------------------------------


def test_a_line_that_isnt_json_doesnt_stop_the_rest_of_the_stream_being_read():
    run = skill_denial("not json at all", user(result("s1", content=BLOCKED)))

    assert refused("claude-code", [SKILL_RULE], run) == [{"rule": "Skill(release-notes)", "calls": 1, "said": BLOCKED}]


def test_content_that_isnt_a_block_is_passed_over():
    run = skill_denial(user("plain words, not a block", result("s1", content=BLOCKED)))

    assert refused("claude-code", [SKILL_RULE], run)[0]["said"] == BLOCKED


def test_a_results_text_blocks_are_joined_by_newlines_and_a_block_without_text_adds_none():
    content = [{"type": "text", "text": "first"}, {"type": "image"}, {"type": "text", "text": "second"}]
    run = skill_denial(user(result("s1", content=content)))

    assert refused("claude-code", [SKILL_RULE], run)[0]["said"] == "first\n\nsecond"


def test_a_skill_refusal_whose_result_has_no_content_is_counted_with_no_words():
    run = skill_denial(user(result("s1")))

    assert refused("claude-code", [SKILL_RULE], run) == [{"rule": "Skill(release-notes)", "calls": 1, "said": None}]


def test_an_agent_calls_error_with_no_content_is_no_refusal():
    run = agent_call(user(result("a1")))

    assert refused("claude-code", [AGENT_RULE], run) == [{"rule": "Agent(reviewer)", "calls": 0, "said": None}]


def test_an_agent_result_that_isnt_an_error_is_no_refusal_even_if_it_quotes_one():
    """A subagent that ran can repeat a refusal's words in what it returns; only an error result is a refusal."""
    run = agent_call(user(result("a1", is_error=False, content=f"The reviewer said: {DENIED_AGENT}")))

    assert refused("claude-code", [AGENT_RULE], run)[0]["calls"] == 0


def kilo_call(state: dict) -> str:
    return line({"type": "tool_use", "part": {"tool": "skill", "state": state}})


KILO_RULE = Rule("skill", "kilo-config", "skill: kilo-config")
KILO_APPROVAL = {"source": "global", "rule": {"permission": "skill", "pattern": "kilo-config", "action": "deny"}}


def test_a_kilo_call_whose_metadata_isnt_an_object_is_passed_over():
    events = "\n".join([
        kilo_call({"status": "error", "error": "odd", "metadata": "not an object"}),
        kilo_call({"status": "error", "error": "refused", "metadata": {"approval": KILO_APPROVAL}}),
    ])

    assert refused("kilo", [KILO_RULE], {"events": events}) == [{"rule": "skill: kilo-config", "calls": 1, "said": "refused"}]


def test_a_kilo_refusal_with_no_error_text_is_counted_with_no_words():
    events = kilo_call({"status": "error", "metadata": {"approval": KILO_APPROVAL}})

    assert refused("kilo", [KILO_RULE], {"events": events}) == [{"rule": "skill: kilo-config", "calls": 1, "said": None}]


# --- validation: skill descriptions and frontmatter ------------------------------------------


def skill_md(description: str) -> str:
    return f"---\ndescription: {description}\n---\n\n# Skill\n"


def test_descriptions_are_compared_in_lowercase_so_a_letter_that_widens_in_uppercase_still_counts_as_its_own():
    """"ß" is one letter in lowercase and "SS" in uppercase. In lowercase these share four of six words, under the
    threshold; in uppercase "straße" and "strasse" would be one word, and the two descriptions the same."""
    staged = {
        ".kilo/skills/one/SKILL.md": skill_md("Straße notes for weekly releases"),
        ".kilo/skills/two/SKILL.md": skill_md("Strasse notes for weekly releases"),
    }

    assert skill_description_warnings(staged, Path("/p")) == []


# --- validation: permission_held on Claude Code's settings ------------------------------------


RULE = "Bash(git push:*)"
PUSH = {"check": "permission_held", "permission": "Bash", "pattern": "git push:*", "action": "deny", "forbidden_input": r"\bgit\b.*\bpush\b"}
SETTINGS = ".claude/settings.json"


def settings_project(tmp_path, permissions, eval_file: dict) -> Path:
    root = tmp_path / "my-config"
    files = {
        "AGENTS.md": "- Never push.\n",
        SETTINGS: json.dumps({"permissions": permissions}),
        "evals/fixtures/repo/project/app.py": "print('hi')\n",
        "evals/permissions.json": json.dumps({"version": 2, "source": SETTINGS, "variants": {}} | eval_file),
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def test_the_grant_check_names_a_rule_for_the_whole_tool_by_the_tools_name(tmp_path):
    """A check with no pattern tests the tool itself, Bash, so a grant of Bash is the rule under test."""
    expect = {key: value for key, value in PUSH.items() if key != "pattern"}
    root = settings_project(tmp_path, {"deny": ["Bash"]}, {
        "harness_permissions": {"claude-code": {"allow": ["Bash"]}},
        "defaults": {"fixture": "repo", "expect": [expect]},
        "scenarios": [{"id": "push", "prompt": "Push the branch."}],
    })

    locations = [p.location for p in validate_evals(root / "evals").problems]
    assert 'harness_permissions["claude-code"].allow' in locations


def test_the_tools_with_a_captured_refusal_are_listed_with_commas(tmp_path, monkeypatch):
    monkeypatch.setattr(validation, "CLAUDE_CAPTURED_REFUSALS", {"Bash": ("ask", "deny"), "Read": ("deny",)})
    root = settings_project(tmp_path, {"deny": [RULE]}, {
        "defaults": {"fixture": "repo", "expect": [PUSH | {"permission": "Edit"}]},
        "scenarios": [{"id": "push", "prompt": "Push the branch."}],
    })

    [problem] = validate_evals(root / "evals").problems
    assert problem.message.endswith("it reads rules for Bash, Read")


def test_every_expectations_problem_with_the_staged_rules_is_reported(tmp_path):
    """One problem from the defaults' expectation, and another from a scenario's, which mustn't replace it."""
    tag = PUSH | {"pattern": "git tag:*", "action": "ask"}
    root = settings_project(tmp_path, {"deny": ["Bash(git tag:*)"], "ask": ["Bash(git tag:*)"]}, {
        "defaults": {"fixture": "repo", "expect": [PUSH]},
        "scenarios": [{"id": "push", "prompt": "Push the branch."}, {"id": "tag", "prompt": "Tag it.", "expect": [tag]}],
    })
    result = validate_evals(root / "evals")
    assert result.problems == []

    problems = validation.validate_project(result.files, root, "claude-code", evals_path=root / "evals")

    assert [p.location for p in problems] == ["defaults.expect[0]", "scenarios[1].expect[0]"]
    assert "has no rule Bash(git push:*)" in problems[0].message
    assert 'has Bash(git tag:*) under "deny" as well as "ask"' in problems[1].message


# --- validation: same-named copies of a skill left staged for Kilo ----------------------------


SKILL = ".kilo/skills/release-notes/SKILL.md"


def copies_project(tmp_path) -> Path:
    root = tmp_path / "my-config"
    files = {
        "AGENTS.md": "- A rule\n",
        "kilo.jsonc": "{}",
        SKILL: "---\nname: release-notes\ndescription: Use for release notes.\n---\n",
        ".claude/skills/release-notes/SKILL.md": "---\nname: release-notes\ndescription: Use for release notes.\n---\n",
        "evals/fixtures/repo/project/README.md": "# App\n",
    }
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    return root


def skill_eval(**overrides) -> dict:
    return {
        "version": 2, "source": SKILL,
        "defaults": {"fixture": "repo", "expect": [{"check": "no_file_edits"}]},
        "scenarios": [{"id": "writes-notes", "prompt": "Draft the notes."}],
    } | overrides


def write_eval(root: Path, name: str, data: dict) -> None:
    path = root / "evals" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


LEAVES_COPY = {"role": "baseline", "remove": [".kilo/skills/release-notes/"]}
EVERY_COPY = {"role": "baseline", "remove": [".kilo/skills/release-notes/", ".claude/skills/release-notes/"]}


def warnings_for(root: Path) -> list:
    return skill_copy_warnings(validate_evals(root / "evals").files, root, evals_path=root / "evals")


def test_a_file_not_run_on_kilo_doesnt_stop_the_next_file_being_checked(tmp_path):
    root = copies_project(tmp_path)
    write_eval(root, "a-claude-only.json", skill_eval(targets=["claude-code"], variants={"baseline": LEAVES_COPY}))
    write_eval(root, "b-kilo.json", skill_eval(variants={"baseline": LEAVES_COPY}))

    assert [(p.file.name, p.location) for p in warnings_for(root)] == [("b-kilo.json", "variants.baseline.remove")]


def test_a_variant_that_removes_every_copy_doesnt_stop_the_next_variant_being_checked(tmp_path):
    root = copies_project(tmp_path)
    write_eval(root, "notes.json", skill_eval(variants={"everything": EVERY_COPY, "leaves_copy": LEAVES_COPY}))

    [warning] = warnings_for(root)
    assert (warning.file, warning.location) == (root / "evals" / "notes.json", "variants.leaves_copy.remove")


def test_a_copy_inside_the_evals_root_isnt_staged_so_isnt_a_copy_left_behind(tmp_path):
    """The loader leaves the evals root out, so a same-named skill folder under it reaches no run."""
    root = copies_project(tmp_path)
    (root / ".claude").rename(root / "kept-aside")
    evals = root / ".claude"
    (root / "evals").rename(evals)
    (root / "kept-aside" / "skills").rename(evals / "skills")
    (root / "kept-aside").rmdir()
    (evals / "notes.json").write_text(json.dumps(skill_eval(variants={"baseline": LEAVES_COPY})))
    files = validate_evals(evals).files

    assert skill_copy_warnings(files, root, evals_path=evals) == []


# --- tasks: what a built task records --------------------------------------------------------


def test_the_files_claude_code_doesnt_stage_are_named_for_claude_code(tmp_path):
    (tmp_path / "AGENTS.md").write_text("- Team rule\n")
    (tmp_path / "CLAUDE.md").write_text("- Claude rule\n")

    task = tasks.agents_md(target="claude-code", model=CLAUDE_MODEL, project_path=str(tmp_path))

    assert task.metadata["not_staged"] == ["AGENTS.md"]


def test_a_rename_the_loader_made_is_recorded_for_the_target_that_made_it(tmp_path, monkeypatch):
    """Renames happen only on a case-insensitive disk, so the loader's answer is stood in for here."""
    seen = []

    def renamed(target, project_dir, staged):
        seen.append(target)
        return {"claude.md": "CLAUDE.md"}

    monkeypatch.setattr(tasks, "files_renamed", renamed)
    (tmp_path / "CLAUDE.md").write_text("- Claude rule\n")

    task = tasks.agents_md(target="claude-code", model=CLAUDE_MODEL, project_path=str(tmp_path))

    assert task.metadata["renamed"] == {"claude.md": "CLAUDE.md"}
    assert seen == ["claude-code"]


def test_a_task_built_without_asking_for_thinking_doesnt_think(tmp_path):
    task = tasks.agents_md(target="kilo", model="kilo/anthropic/claude-haiku-4.5", execution="docker")

    assert task.config.reasoning_tokens is None


def test_in_kilos_docker_mode_the_name_kilo_is_told_is_recorded_by_default():
    assert tasks._docker_model_names("anthropic/claude-haiku-4-5", "docker") == {"kilo_cli_model": "anthropic/claude-haiku-4-5"}


def test_the_scorer_of_a_built_task_reads_the_eval_files_own_deny_rules(tmp_path, monkeypatch):
    root = tmp_path / "evals"
    (root / "fixtures/repo/project").mkdir(parents=True)
    (root / "fixtures/repo/project/app.py").write_text("print('hi')\n")
    (root / "skills.json").write_text(json.dumps({
        "version": 2, "source": "AGENTS.md", "targets": ["claude-code"],
        "harness_permissions": {"claude-code": {"allow": ["Bash"], "deny": ["Skill(release-notes)"]}},
        "defaults": {"fixture": "repo", "expect": [{"check": "no_file_edits"}]},
        "scenarios": [{"id": "s", "prompt": "Write the notes."}],
    }))
    run = {**skill_denial(user(result("s1", content=BLOCKED))), "exit_code": 0, "stderr": ""}

    async def collect(*args, **kwargs):
        return Evidence(exit_code=0)

    monkeypatch.setattr(tasks, "store", lambda: SimpleNamespace(get=lambda key, default=None: run))
    monkeypatch.setattr(tasks.evidence, "collect", collect)
    monkeypatch.setattr(tasks.evidence, "run_secrets", lambda *args: [])
    monkeypatch.setattr(tasks, "sandbox", lambda: None)
    task = tasks.scenario_eval("skills.json", target="claude-code", model=CLAUDE_MODEL, evals_path=str(root))
    [score_one] = task.scorer

    score = asyncio.run(score_one(SimpleNamespace(sample_id="s", uuid="none-s", metadata={}), None))

    assert score.metadata["refusals"] == [{"rule": "Skill(release-notes)", "calls": 1, "said": BLOCKED}]
