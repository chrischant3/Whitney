"""Variants: a variant's `remove` and `set` applied to the staged files (whitney.variants).

The shipped tasks' variants come from their eval files through the version 1
upgrade, so these tests also pin that today's `baseline` and `allow_control`
stage the same files they always did.
"""

import asyncio
import json
from pathlib import Path

import pytest
from inspect_ai._eval.task.sandbox import read_sandboxenv_file

from whitney import tasks as tasks_module
from whitney import variants as variants_module
from whitney.evalfile import EvalFile, V1File, Variant, load, read
from whitney.project_files import load_project_files, set_external_directory, without_instructions
from whitney.static_checks import parse_jsonc
from whitney.tasks import agents_md, kilo_jsonc
from whitney.variants import (
    TEMPLATE,
    PointerError,
    VariantError,
    apply_variant,
    changes_nothing,
    declared_variants,
    default_baseline,
    for_claude_code_instructions,
    pointer_from,
    pointer_tokens,
    repeated_removes,
    shadowed_sets,
    spread_over_kilo_configs,
)

MODEL = "anthropic/claude-haiku-4-5"
EVALS = Path(__file__).parents[1] / "evals"
CONFIG = '{\n  // a comment\n  "permission": {"external_directory": "ask"},\n}\n'


def variant(**fields) -> Variant:
    return Variant.model_validate(fields)


def _staged(value: str) -> str:
    return asyncio.run(read_sandboxenv_file(value)).decode("utf-8")


def _project_files(sample) -> dict[str, str]:
    return {path.removeprefix("project/"): _staged(text) for path, text in sample.files.items() if path.startswith("project/")}


def _without(document, tokens):
    """`document` with the value at `tokens` taken out, written here rather than with whitney.variants."""
    if not tokens:
        return None
    copy = dict(document)
    head, *rest = tokens
    if rest:
        copy[head] = _without(copy[head], rest)
    else:
        copy.pop(head)
    return copy


def _at(document, tokens):
    for token in tokens:
        document = document[token]
    return document


# --- Each shipped variant differs from the template by exactly its declared changes ----------

SHIPPED = [
    (agents_md, EVALS / "agents-md.json", "kilo", "baseline"),
    (agents_md, EVALS / "agents-md.json", "claude-code", "baseline"),
    (kilo_jsonc, EVALS / "configuration" / "kilo-jsonc.json", "kilo", "allow_control"),
]


@pytest.mark.parametrize("build, path, target, name", SHIPPED, ids=lambda value: getattr(value, "__name__", str(value)))
def test_each_variants_staged_files_differ_from_the_templates_by_exactly_its_declared_changes(build, path, target, name):
    declared = declared_variants(load(path))[name]
    template = {s.id: _project_files(s) for s in build(target=target, model=MODEL, variant=TEMPLATE).dataset}
    changed = {s.id: _project_files(s) for s in build(target=target, model=MODEL, variant=name).dataset}

    assert changed.keys() == template.keys()
    for scenario, files in changed.items():
        before = template[scenario]
        removed = sorted(before.keys() - files.keys())
        assert removed == sorted(declared.remove), scenario
        assert files.keys() <= before.keys()
        rewritten = sorted(p for p in files if files[p] != before[p])
        assert rewritten == sorted(declared.set_pointers), scenario
        for file, pointers in declared.set_pointers.items():
            after, original = parse_jsonc(files[file]), parse_jsonc(before[file])
            for pointer, value in pointers.items():
                assert _at(after, pointer_tokens(pointer)) == value
            for pointer in pointers:
                after, original = _without(after, pointer_tokens(pointer)), _without(original, pointer_tokens(pointer))
            assert after == original, f"{scenario}: {file} changed beyond its pointers"


def test_the_version_1_files_upgrade_to_todays_baseline_and_allow_control():
    assert declared_variants(load(EVALS / "agents-md.json")) == {"baseline": variant(role="baseline", remove=["AGENTS.md"])}
    assert declared_variants(load(EVALS / "configuration" / "kilo-jsonc.json")) == {
        "allow_control": variant(role="control", set={"kilo.jsonc": {"/permission/external_directory": "allow"}})
    }


def test_a_version_1_file_as_read_has_the_variants_its_upgrade_gives():
    for path in (EVALS / "agents-md.json", EVALS / "configuration" / "kilo-jsonc.json"):
        assert declared_variants(read(path)) == declared_variants(load(path))
    no_tokens = V1File.model_validate({"source": "kilo.jsonc", "scenarios": [{"id": "a", "prompt": "p", "fixture": "f"}]})
    assert declared_variants(no_tokens) == {"baseline": default_baseline("kilo.jsonc")}


def test_set_rewrites_the_file_as_todays_json_dumps_with_no_trailing_newline():
    updated = apply_variant({"kilo.jsonc": CONFIG}, variant(set={"kilo.jsonc": {"/permission/external_directory": "allow"}}))

    assert updated["kilo.jsonc"] == json.dumps({"permission": {"external_directory": "allow"}}, indent=2)


# --- The allow_control wrapper: every staged Kilo config, as set_external_directory did ------


def _project(tmp_path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(text)
    return tmp_path


@pytest.mark.parametrize("files", [
    {"kilo.jsonc": CONFIG},
    {".kilo/kilo.jsonc": CONFIG},
    {"kilo.jsonc": CONFIG, ".kilo/kilo.jsonc": '{"permission": {"external_directory": "deny", "bash": "ask"}}'},
    {"kilo.json": '{"model": "m"}'},
    {"kilo.jsonc": '{"permission": {}}', ".kilocode/kilo.json": '{"$schema": "x"}'},
], ids=["root", "dot-kilo-only", "both", "no-permission", "empty-permission"])
def test_allow_control_stages_what_set_external_directory_did(tmp_path, files):
    project = _project(tmp_path, {"AGENTS.md": "- rule\n", **files})

    task = kilo_jsonc(target="kilo", model=MODEL, variant="allow_control", project_path=str(project))

    expected = set_external_directory(load_project_files(project), "allow")
    assert task.metadata["evaluation_assets"] == expected
    for sample in task.dataset:
        staged = _project_files(sample)
        assert {path: staged[path] for path in expected} == expected


def test_the_allow_control_wrapper_needs_a_staged_kilo_config():
    control = variant(role="control", set={"kilo.jsonc": {"/permission/external_directory": "allow"}})
    with pytest.raises(ValueError, match="^no Kilo config file found in project assets$"):
        tasks_module._on_every_kilo_config(control, {"AGENTS.md": "x"}, "kilo.jsonc")


def test_the_spread_leaves_a_non_object_parent_for_set_to_refuse():
    control = variant(role="control", set={"kilo.jsonc": {"/permission/external_directory": "allow"}})
    spread = spread_over_kilo_configs(control, {"kilo.jsonc": '{"permission": "ask"}'}, "kilo.jsonc")

    with pytest.raises(VariantError, match="/permission isn't an object"):
        apply_variant({"kilo.jsonc": '{"permission": "ask"}'}, spread)


def test_the_spread_creates_every_missing_parent_as_today_s_setdefault_did():
    control = variant(role="control", set={"kilo.jsonc": {"/a/b/c": 1, "/x/y": 2, "/x/q/r": 3, "/s/t/u": 4}})
    files = {"kilo.jsonc": '{"x": {"z": 0}, "s": "ask"}'}

    spread = spread_over_kilo_configs(control, files, "kilo.jsonc")

    assert spread.set_pointers == {"kilo.jsonc": {"/a": {"b": {"c": 1}}, "/x/y": 2, "/x/q": {"r": 3}, "/s/t/u": 4}}
    without_s = spread.model_copy(update={"set_pointers": {"kilo.jsonc": {
        p: v for p, v in spread.set_pointers["kilo.jsonc"].items() if p != "/s/t/u"
    }}})
    assert json.loads(apply_variant(files, without_s)["kilo.jsonc"]) == {
        "x": {"z": 0, "y": 2, "q": {"r": 3}}, "s": "ask", "a": {"b": {"c": 1}}
    }


def test_the_spread_keeps_siblings_under_one_missing_parent():
    control = variant(role="control", set={"kilo.jsonc": {
        "/permission/external_directory": "allow", "/permission/bash": "allow", "/permission/edit/x/y": "deny",
        "/permission/edit/x/z": "ask",
    }})

    spread = spread_over_kilo_configs(control, {"kilo.jsonc": "{}"}, "kilo.jsonc")

    assert json.loads(apply_variant({"kilo.jsonc": "{}"}, spread)["kilo.jsonc"]) == {"permission": {
        "external_directory": "allow", "bash": "allow", "edit": {"x": {"y": "deny", "z": "ask"}},
    }}


def test_a_moved_pointer_after_a_value_at_its_parent_replaces_it_as_setting_them_in_turn_would():
    control = variant(role="control", set={"kilo.jsonc": {"/permission": "ask", "/permission/bash": "allow"}})

    spread = spread_over_kilo_configs(control, {"kilo.jsonc": "{}"}, "kilo.jsonc")

    assert spread.set_pointers == {"kilo.jsonc": {"/permission": {"bash": "allow"}}}


def test_the_spread_keeps_other_files_and_configs_the_variant_names_itself():
    control = variant(role="control", set={
        "kilo.jsonc": {"/permission/external_directory": "allow"},
        ".kilo/kilo.jsonc": {"/permission/external_directory": "deny"},
        "notes.json": {"/a": 1},
    })
    files = {"kilo.jsonc": CONFIG, ".kilo/kilo.jsonc": CONFIG, "kilo.json": "{}", "notes.json": "{}"}

    spread = spread_over_kilo_configs(control, files, "kilo.jsonc")

    assert spread.set_pointers == {
        "kilo.jsonc": {"/permission/external_directory": "allow"},
        ".kilo/kilo.jsonc": {"/permission/external_directory": "deny"},
        "notes.json": {"/a": 1},
        "kilo.json": {"/permission": {"external_directory": "allow"}},
    }


@pytest.mark.parametrize("source, sets, files", [
    ("AGENTS.md", {"kilo.jsonc": {"/a": 1}}, {"kilo.jsonc": "{}", "kilo.json": "{}"}),
    ("kilo.jsonc", {"kilo.json": {"/a": 1}}, {"kilo.jsonc": "{}", "kilo.json": "{}"}),
    ("kilo.jsonc", {"kilo.jsonc": {"/a": 1}}, {"AGENTS.md": "x"}),
], ids=["source-not-a-config", "source-not-set", "no-config-staged"])
def test_the_spread_changes_nothing_it_has_no_source_pointers_or_configs_for(source, sets, files):
    control = variant(role="control", set=sets)

    assert spread_over_kilo_configs(control, files, source) == control


def test_the_spread_leaves_an_unreadable_config_for_set_to_refuse():
    control = variant(role="control", set={"kilo.jsonc": {"/permission/external_directory": "allow"}})

    spread = spread_over_kilo_configs(control, {"kilo.jsonc": "{"}, "kilo.jsonc")

    assert spread == control


def test_the_agents_md_baseline_of_a_claude_md_only_project_runs_with_nothing_staged(tmp_path):
    # The CLAUDE.md is staged under its own name, and the baseline's remove of AGENTS.md removes it.
    project = _project(tmp_path, {"CLAUDE.md": "- rule\n"})

    task = agents_md(target="claude-code", model=MODEL, variant="baseline", project_path=str(project))

    assert task.metadata["evaluation_assets"] == {}



def test_the_agents_md_baseline_still_runs_when_a_kilo_project_has_no_agents_md(tmp_path):
    # Today's baseline removes AGENTS.md whether or not it was staged, so a project without one still runs.
    project = _project(tmp_path, {"kilo.jsonc": "{}"})

    task = agents_md(target="kilo", model=MODEL, variant="baseline", project_path=str(project))

    assert set(task.metadata["evaluation_assets"]) == {"kilo.jsonc"}

# --- remove ----------------------------------------------------------------------------------


def test_remove_leaves_out_a_file_and_a_folder_ending_in_a_slash():
    files = {"AGENTS.md": "a", ".kilo/skills/x/SKILL.md": "s", ".kilo/skills/x/ref.md": "r", ".kilo/skills/xy/SKILL.md": "t"}

    updated = apply_variant(files, variant(remove=["AGENTS.md", ".kilo/skills/x/"]))

    assert updated == {".kilo/skills/xy/SKILL.md": "t"}
    assert files["AGENTS.md"] == "a", "the staged files passed in are not changed"


@pytest.mark.parametrize("remove, expected", [
    (["AGENTS.md", "AGENTS.md"], [(("remove", 1), "'AGENTS.md' is already removed by remove[0] ('AGENTS.md'); list each path once")]),
    ([".kilo/", ".kilo/kilo.jsonc"], [(("remove", 1), "'.kilo/kilo.jsonc' is already removed by remove[0] ('.kilo/'); list each path once")]),
    ([".kilo/kilo.jsonc", ".kilo/"], [(("remove", 1), "'.kilo/' also removes remove[0] ('.kilo/kilo.jsonc'); list each path once")]),
    (["AGENTS.md", ".kilo/kilo.jsonc"], []),
    ([".kilo/", ".kilocode/"], []),
])
def test_a_repeated_or_overlapping_remove_is_found_and_still_applies(remove, expected):
    files = {"AGENTS.md": "a", ".kilo/kilo.jsonc": "{}", ".kilocode/x": "y"}

    assert repeated_removes(variant(remove=remove)) == expected
    removed = {path for path in files if any(path == r or (r.endswith("/") and path.startswith(r)) for r in remove)}
    assert apply_variant(files, variant(remove=remove)) == {p: t for p, t in files.items() if p not in removed}


def test_removing_a_path_that_is_not_staged_is_refused_with_its_place():
    with pytest.raises(VariantError) as error:
        apply_variant({"AGENTS.md": "a"}, variant(remove=["AGENTS.md", "CLAUDE.md", ".kilo/skills/"]))

    assert error.value.problems == [
        (("remove", 1), "'CLAUDE.md' isn't staged, so removing it changes nothing; staged files: AGENTS.md"),
        (("remove", 2), "'.kilo/skills/' isn't staged, so removing it changes nothing; staged files: AGENTS.md"),
    ]
    assert str(error.value) == f"remove[1]: {error.value.problems[0][1]}; remove[2]: {error.value.problems[1][1]}"


# --- set and JSON Pointer --------------------------------------------------------------------


def test_set_puts_each_value_at_its_pointer_and_keeps_the_rest():
    files = {"kilo.jsonc": '{"permission": {"bash": {"git push *": "ask"}}, "model": "m"}', "AGENTS.md": "a"}

    updated = apply_variant(files, variant(set={"kilo.jsonc": {"/permission/bash/git push *": "deny", "/share": "disabled"}}))

    assert json.loads(updated["kilo.jsonc"]) == {"permission": {"bash": {"git push *": "deny"}}, "model": "m", "share": "disabled"}
    assert updated["AGENTS.md"] == "a"


def test_pointer_escapes_are_decoded_as_rfc_6901_says():
    assert pointer_tokens("/a~1b/c~0d/~01/") == ["a/b", "c~d", "~1", ""]
    assert pointer_from(["a/b", "c~d", "~1", ""]) == "/a~1b/c~0d/~01/"
    updated = apply_variant({"c.json": '{"a/b": {}}'}, variant(set={"c.json": {"/a~1b/~0": 1}}))
    assert json.loads(updated["c.json"]) == {"a/b": {"~": 1}}


@pytest.mark.parametrize("pointer, message", [
    ("/a~2", "'/a~2': ~ must be followed by 0 or 1 (~0 is ~, ~1 is /)"),
    ("/a~", "'/a~': ~ must be followed by 0 or 1 (~0 is ~, ~1 is /)"),
    ("a", "'a' must start with /"),
])
def test_a_badly_escaped_pointer_is_refused(pointer, message):
    with pytest.raises(PointerError) as error:
        pointer_tokens(pointer)
    assert str(error.value) == message


@pytest.mark.parametrize("document, pointer, message", [
    ('{"permission": "ask"}', "/permission/bash", "/permission isn't an object, so set can't put /permission/bash inside it"),
    ('{"permission": ["ask"]}', "/permission/0", "/permission isn't an object, so set can't put /permission/0 inside it"),
    ("[1]", "/0", "the file isn't an object, so set can't put /0 inside it"),
    ('{"a": {}}', "/a/b/c", "/a/b doesn't exist, so set can't put /a/b/c inside it; set /a/b to an object instead"),
])
def test_a_pointer_into_a_non_object_or_a_missing_parent_is_refused(document, pointer, message):
    with pytest.raises(VariantError) as error:
        apply_variant({"c.json": document}, variant(set={"c.json": {pointer: 1}}))
    assert error.value.problems == [(("set", "c.json", pointer), message)]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {"deep": [float("-inf")]}])
def test_a_value_json_cannot_hold_is_refused_with_its_place(value):
    with pytest.raises(VariantError) as error:
        apply_variant({"c.json": "{}"}, variant(set={"c.json": {"/a": value}}))

    [(where, message)] = error.value.problems
    assert where == ("set", "c.json")
    assert message.startswith("'c.json' can't be written as JSON after set (")
    assert message.endswith("); set values must be JSON: no NaN or Infinity")


def test_set_on_a_file_that_is_not_staged_or_not_json_or_removed_is_refused():
    files = {"kilo.jsonc": "{", "AGENTS.md": "a", ".kilo/kilo.jsonc": "{}"}
    with pytest.raises(VariantError) as error:
        apply_variant(files, variant(
            remove=[".kilo/"],
            set={"kilo.json": {"/a": 1}, "kilo.jsonc": {"/a": 1}, ".kilo/kilo.jsonc": {"/a": 1}},
        ))

    problems = dict(error.value.problems)
    assert problems[("set", "kilo.json")] == (
        "'kilo.json' isn't staged, so set can't change it; staged files: .kilo/kilo.jsonc, AGENTS.md, kilo.jsonc"
    )
    assert problems[("set", "kilo.jsonc")].startswith("'kilo.jsonc' isn't JSON or JSONC (")
    assert problems[("set", ".kilo/kilo.jsonc")] == "'.kilo/kilo.jsonc' is also removed by this variant, so set can't change it"
    assert len(problems) == 3


# --- Default variants ------------------------------------------------------------------------


def test_the_default_baseline_removes_the_source_or_a_skills_whole_folder():
    assert default_baseline("AGENTS.md") == variant(role="baseline", remove=["AGENTS.md"])
    assert default_baseline(".kilo/skills/review/SKILL.md") == variant(role="baseline", remove=[".kilo/skills/review/"])
    assert default_baseline(".kilo/skills/review/notes.md") == variant(role="baseline", remove=[".kilo/skills/review/notes.md"])


def test_declared_variants_are_the_files_own_or_the_default_baseline():
    base = {"version": 2, "source": "AGENTS.md", "defaults": {"fixture": "f", "expect": [{"check": "c"}]}, "scenarios": [{"id": "a", "prompt": "p"}]}
    assert declared_variants(EvalFile.model_validate(base)) == {"baseline": default_baseline("AGENTS.md")}
    assert declared_variants(EvalFile.model_validate({**base, "variants": {}})) == {}
    own = {"strict": {"role": "control", "remove": ["AGENTS.md"]}}
    assert declared_variants(EvalFile.model_validate({**base, "variants": own})) == {"strict": variant(**own["strict"])}


# --- What validation asks of the result ------------------------------------------------------


def test_a_variant_that_changes_nothing_is_found():
    files = {"kilo.jsonc": CONFIG, "AGENTS.md": "a"}
    same = variant(set={"kilo.jsonc": {"/permission/external_directory": "ask"}})
    different = variant(set={"kilo.jsonc": {"/permission/external_directory": "allow"}})

    assert changes_nothing(files, apply_variant(files, same))
    assert not changes_nothing(files, apply_variant(files, different))
    assert not changes_nothing(files, apply_variant(files, variant(remove=["AGENTS.md"])))
    assert changes_nothing(files, apply_variant(files, variant()))


@pytest.mark.parametrize("staged, value", [("true", 1), ("1", True), ("1.0", 1), ("1", 1.0)])
def test_a_value_of_another_json_type_is_a_change_and_a_shadow(staged, value):
    files = {"kilo.jsonc": f'{{"a": {staged}}}', ".kilo/kilo.jsonc": f'{{"a": {staged}}}'}
    one = variant(set={"kilo.jsonc": {"/a": value}})

    assert not changes_nothing(files, apply_variant(files, one))
    assert [where for where, _ in shadowed_sets(files, one)] == [("set", "kilo.jsonc", "/a")]


def test_key_order_is_not_a_change():
    files = {"c.json": '{"a": {"x": 1, "y": 2}}'}

    assert changes_nothing(files, apply_variant(files, variant(set={"c.json": {"/a": {"y": 2, "x": 1}}})))


def test_a_set_another_staged_kilo_config_could_shadow_is_found():
    files = {"kilo.jsonc": CONFIG, ".kilo/kilo.jsonc": '{"permission": {"external_directory": "deny"}}'}
    control = variant(set={"kilo.jsonc": {"/permission/external_directory": "allow"}})

    assert shadowed_sets(files, control) == [(
        ("set", "kilo.jsonc", "/permission/external_directory"),
        "'.kilo/kilo.jsonc' also sets /permission/external_directory, and Kilo reads both files, so it could "
        "shadow this change; set the same value there too, or remove '.kilo/kilo.jsonc' in this variant",
    )]


def test_a_non_object_ancestor_in_another_kilo_config_shadows_too():
    files = {"kilo.jsonc": CONFIG, "kilo.json": '{"permission": "ask"}'}
    control = variant(set={"kilo.jsonc": {"/permission/external_directory": "allow"}})

    [(where, message)] = shadowed_sets(files, control)

    assert message.startswith("'kilo.json' also sets /permission, and Kilo reads both files")


@pytest.mark.parametrize("other, change", [
    ('{"permission": {"bash": "ask"}}', {}),
    ('{"permission": {"external_directory": "allow"}}', {}),
    ('{"permission": {"external_directory": "deny"}}', {".kilo/kilo.jsonc": {"/permission/external_directory": "allow"}}),
    ('{"permission": {"external_directory": "deny"}}', {"remove": True}),
    ("not json", {}),
    ('["external_directory"]', {}),
], ids=["other-key", "same-value", "set-there-too", "removed-there", "unreadable", "not-an-object"])
def test_another_kilo_config_that_cannot_shadow_the_set_is_not_found(other, change):
    files = {"kilo.jsonc": CONFIG, ".kilo/kilo.jsonc": other}
    sets = {"kilo.jsonc": {"/permission/external_directory": "allow"}, **{k: v for k, v in change.items() if k != "remove"}}
    control = variant(set=sets, remove=[".kilo/kilo.jsonc"] if change.get("remove") else [])

    assert shadowed_sets(files, control) == []


def test_a_later_kilo_config_is_checked_when_an_earlier_one_is_not_staged():
    files = {"kilo.json": "{}", ".kilo/kilo.jsonc": '{"a": 2}'}

    [(where, message)] = shadowed_sets(files, variant(set={"kilo.json": {"/a": 1}}))

    assert message.startswith("'.kilo/kilo.jsonc' also sets /a,")


def test_a_set_that_overwrites_its_own_pointer_is_not_shadowing():
    files = {"kilo.jsonc": '{"p": {}}'}

    assert shadowed_sets(files, variant(set={"kilo.jsonc": {"/p/a": 1, "/p": "x"}})) == []


def test_a_listing_of_no_staged_files_says_none():
    with pytest.raises(VariantError, match="staged files: none$"):
        apply_variant({}, variant(remove=["AGENTS.md"]))


def test_a_set_on_a_file_kilo_does_not_read_as_config_is_never_shadowed():
    files = {"other.json": "{}", "kilo.jsonc": '{"a": 2}'}

    assert shadowed_sets(files, variant(set={"other.json": {"/a": 1}})) == []
    [(where, _)] = shadowed_sets({**files, "kilo.json": "{}"}, variant(set={"other.json": {"/a": 1}, "kilo.json": {"/a": 1}}))
    assert where == ("set", "kilo.json", "/a")


# The claude-code target stages CLAUDE.md, .claude/CLAUDE.md, AGENTS.md or .claude/AGENTS.md under their own
# names, so a remove of AGENTS.md removes every one staged and a remove of any other name removes that file only
# (tasks/plan-phase5.md, decision 8).


def test_a_remove_of_an_instruction_file_widens_to_every_one_claude_code_was_given():
    staged = {"CLAUDE.md": "a\n", ".claude/CLAUDE.md": "b\n", "notes.md": "c\n"}
    variant = Variant(remove=["AGENTS.md", "notes.md", "CLAUDE.md"])

    widened = for_claude_code_instructions(variant, staged)

    assert widened.remove == ["CLAUDE.md", ".claude/CLAUDE.md", "notes.md"]
    assert apply_variant(staged, widened) == {}


def test_a_variant_without_an_instruction_file_remove_is_kept_as_it_is():
    staged = {"AGENTS.md": "a\n", "notes.md": "c\n"}
    variant = Variant(remove=["notes.md"])

    assert for_claude_code_instructions(variant, staged) is variant
    assert for_claude_code_instructions(Variant(remove=["AGENTS.md"]), staged).remove == ["AGENTS.md"]


def test_a_remove_of_an_instruction_file_nothing_stands_for_is_kept_for_apply_variant_to_report():
    variant = Variant(remove=["AGENTS.md"])
    assert for_claude_code_instructions(variant, {"notes.md": "c\n"}) is variant


def test_a_remove_of_one_claude_md_removes_only_that_file():
    staged = {"CLAUDE.md": "a\n", ".claude/CLAUDE.md": "b\n"}
    variant = Variant(remove=[".claude/CLAUDE.md"])

    assert for_claude_code_instructions(variant, staged) is variant
    assert apply_variant(staged, variant) == {"CLAUDE.md": "a\n"}


def test_a_remove_of_an_unstaged_dot_claude_agents_md_is_kept_for_apply_variant_to_report():
    variant = Variant(remove=[".claude/AGENTS.md"])
    assert for_claude_code_instructions(variant, {"CLAUDE.md": "a\n"}) is variant


def test_without_instructions_drops_agents_md_for_kilo_and_every_instruction_file_for_claude_code():
    staged = {"AGENTS.md": "a\n", "CLAUDE.md": "b\n", ".claude/AGENTS.md": "c\n", "kilo.jsonc": "{}"}

    assert without_instructions("kilo", staged) == {"CLAUDE.md": "b\n", ".claude/AGENTS.md": "c\n", "kilo.jsonc": "{}"}
    assert without_instructions("claude-code", staged) == {"kilo.jsonc": "{}"}
    assert staged["AGENTS.md"] == "a\n"  # the files given are left as they were


# Kilo reads a skill from any of its skill folders (project_files.KILO_SKILL_FOLDERS), so the automatic
# trigger control and default baseline for a skill, on the kilo target, leave out every staged copy of it
# with the same folder name, not only the source's folder (docs/adding-tests.md, "Testing a skill or an agent").

SKILL_COPIES = {
    ".kilo/skills/release-notes/SKILL.md": "kilo\n",
    ".kilo/skills/release-notes/template.md": "# Notes\n",
    ".kilo/skill/release-notes/SKILL.md": "kilo singular\n",
    ".kilocode/skills/release-notes/SKILL.md": "kilocode\n",
    ".kilocode/skill/release-notes/SKILL.md": "kilocode singular\n",
    ".claude/skills/release-notes/SKILL.md": "claude\n",
    ".agents/skills/release-notes/SKILL.md": "agents\n",
    ".kilo/skills/debugging/SKILL.md": "debugging\n",
    ".claude/skills/debugging/SKILL.md": "debugging copy\n",
    ".claude/skills/release-notes-v2/SKILL.md": "another skill whose name starts the same\n",
    "AGENTS.md": "- rule\n",
}


@pytest.mark.parametrize("source", [".kilo/skills/release-notes/SKILL.md", ".claude/skills/release-notes/SKILL.md"])
def test_kilos_automatic_control_for_a_skill_leaves_out_every_staged_copy_of_it(source):
    widened = variants_module.for_kilo_skill_copies(variants_module.trigger_control(source), SKILL_COPIES, source)

    left = apply_variant(SKILL_COPIES, widened)

    assert not [path for path in left if path.endswith("/release-notes/SKILL.md")]
    assert ".kilo/skills/release-notes/template.md" not in left
    assert sorted(left) == [
        ".claude/skills/debugging/SKILL.md", ".claude/skills/release-notes-v2/SKILL.md",
        ".kilo/skills/debugging/SKILL.md", "AGENTS.md",
    ]
    assert widened.role == "control"


def test_the_widened_remove_names_the_source_first_then_each_staged_copy_once():
    source = ".kilo/skills/release-notes/SKILL.md"
    staged = {path: text for path, text in SKILL_COPIES.items() if not path.startswith(".kilo/skill/")}

    widened = variants_module.for_kilo_skill_copies(default_baseline(source), staged, source)

    assert widened.remove == [
        ".kilo/skills/release-notes/", ".kilocode/skill/release-notes/", ".kilocode/skills/release-notes/",
        ".claude/skills/release-notes/", ".agents/skills/release-notes/",
    ]
    assert widened.role == "baseline"


def test_copies_that_arent_staged_are_not_removed_so_the_control_still_applies():
    source = ".kilo/skills/release-notes/SKILL.md"
    staged = {".kilo/skills/release-notes/SKILL.md": "kilo\n", "AGENTS.md": "- rule\n"}
    control = variants_module.trigger_control(source)

    widened = variants_module.for_kilo_skill_copies(control, staged, source)

    assert widened is control
    assert apply_variant(staged, widened) == {"AGENTS.md": "- rule\n"}


def test_a_source_that_isnt_staged_is_left_for_apply_variant_to_report():
    source = ".kilo/skills/release-notes/SKILL.md"
    staged = {".claude/skills/release-notes/SKILL.md": "claude\n"}

    widened = variants_module.for_kilo_skill_copies(variants_module.trigger_control(source), staged, source)

    assert widened.remove == [".kilo/skills/release-notes/", ".claude/skills/release-notes/"]
    with pytest.raises(VariantError, match="'.kilo/skills/release-notes/' isn't staged"):
        apply_variant(staged, widened)


def test_a_variant_that_doesnt_remove_the_skills_folder_is_kept_as_it_is():
    source = ".kilo/skills/release-notes/SKILL.md"
    own = Variant(remove=["AGENTS.md"])

    assert variants_module.for_kilo_skill_copies(own, SKILL_COPIES, source) is own


def test_a_source_that_isnt_a_skill_is_kept_as_it_is():
    for source in (".kilo/agents/reviewer.md", "AGENTS.md"):
        control = variants_module.trigger_control(source)
        assert variants_module.for_kilo_skill_copies(control, {**SKILL_COPIES, source: "x\n"}, source) is control


def test_only_the_variants_whitney_adds_itself_are_automatic(tmp_path):
    def automatic(**fields):
        data = {
            "version": 2, "source": ".kilo/skills/release-notes/SKILL.md",
            "defaults": {"fixture": "repo", "expect": [{"check": "no_file_edits"}]},
            "scenarios": [{"id": "a", "prompt": "p"}],
        } | fields
        path = tmp_path / "skills" / "release-notes.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data))
        return variants_module.automatic_variants(load(path))

    positive = {"positive": [{"id": "asks", "prompt": "Write the release notes."}]}
    negative = {"negative": [{"id": "asks-the-time", "prompt": "What time is it?"}]}
    assert automatic() == {"baseline"}
    assert automatic(trigger=positive) == {"baseline", "trigger_control"}
    assert automatic(trigger=negative) == {"baseline"}
    assert automatic(trigger=positive, variants={"baseline": {"remove": ["AGENTS.md"]}}) == {"trigger_control"}
    assert automatic(variants={}) == set()
