"""Validation of eval files that needs only the evals path: no project, no model, no harness.

Rules, numbered as in docs/specs/package-and-expected-results.md ("Validation"):

1. The evals path is a folder; the project loads with the target's loader (validate_project).
2. Every eval file matches the schema (version 1 files are upgraded first),
   and eval names and `source` values are unique.
3. Every check and setup step is known, and its parameters are valid; no
   git_commit comes before a git_init.
4. Every fixture is a folder in `fixtures/`, holds no symlinks and is UTF-8 text.
   Its pending/ files stay inside the project, hold nothing under .git/ and
   no NUL; in version 2, apply_pending needs at least one.
5. Every token parameter is findable where its check says, so the check can fail.
   "fixture" means the project the agent starts in (see _searched).
6. The variants half, without the project: no variant is named `template`,
   each one declares a change, and its pointers are well formed. With the
   staged files (validate_variants): its `remove` and `set` targets are
   staged, it changes them, and no other staged Kilo config could shadow
   its `set`. With the project (validate_project): `source` is staged, or
   the problem says which rule left it out, and the variants are checked
   against what the target's loader staged.
7. A `known_gap` starts with an Exceptions ID (`E3: why`); with a constraints
   file, that ID is a row in its Exceptions table.
8. Each Kilo action in `harness_permissions` is "allow", "ask" or "deny",
   alone or per pattern, and the Kilo overlay sets neither "*" nor a key a
   check in the file guards (CheckSpec.guards).
9. A file's `min_pass_rate` isn't below the default minimum of any check it uses.

Each problem names the file and the location, such as
`scenarios[1].expect[0].strings`, so it can be fixed without reading code.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ValidationError

from whitney import shipped_evals
from whitney.checks import CheckSpec, permissions, registry
from whitney.checks.files import KILO_CONFIG_NAMES
from whitney.evalfile import (
    FIXTURES_FOLDER,
    TRIGGER_CONTROL,
    V1_PARAMETER_KEYS,
    VERSION_1_DEPRECATED,
    EvalFile,
    EvalFileError,
    FixtureError,
    Problem,
    ResolvedScenario,
    eval_file_paths,
    fixture_files,
    load,
    location,
    under_test,
)
from whitney.project_files import load_project_files_for, not_staged_reason
from whitney.target_names import TARGETS
from whitney.static_checks import parse_jsonc
from whitney.setup_steps import NOTHING_PENDING, PENDING_DIR, PROJECT_DIR, SETUP_STEPS, order_problems, pending_problem
from whitney.variants import (
    TEMPLATE,
    PointerError,
    VariantError,
    apply_variant,
    changes_nothing,
    declared_variants,
    pointer_tokens,
    repeated_removes,
    shadowed_sets,
    spread_over_kilo_configs,
)

# The fixture folders the agent's project starts from: project/ is staged as it is, pending/ by apply_pending.
PROJECT, PENDING = PROJECT_DIR, PENDING_DIR
KNOWN_GAP = re.compile(r"E\d+: .+")
# Where Kilo 7.8.18 loads a project's skills and agents from, each named by its folder or file
# (docs/spike-findings.md, "Case 2: the same skill in other folders").
STAGED = {
    "skill": re.compile(r"(?:\.kilo|\.kilocode|\.claude|\.agents)/skills?/([^/]+)/SKILL\.md"),
    "agent": re.compile(r"(?:\.kilo|\.kilocode)/agents?/([^/]+)\.md"),
    # Kilo turns the .md files in its workflows folders into commands too (docs/load-order.md).
    "command": re.compile(r"(?:\.kilo|\.kilocode)/(?:commands?|workflows)/([^/]+)\.md"),
}
# The `mode`s an agent was captured running under with `kilo run --agent` (docs/spike-findings.md, "Case 4",
# and "Phase 5 Task 21" for `all`). With `subagent`, or no such agent, Kilo falls back to its default agent.
RUNNABLE_MODES = ("primary", "all")
KILO_ACTIONS = ("allow", "ask", "deny")
PERMISSION_HELD = "permission_held"
# Permission -> the one form of rule a refusal was captured for, and the actions captured in that form
# (docs/spike-findings.md, "Confirmed (permission, action) pairs"). permission_held accepts nothing else.
CAPTURED_REFUSALS = {
    "bash": ("pattern", ("ask", "deny")),
    "edit": ("scalar", ("ask",)),
    "external_directory": ("scalar", ("ask", "deny")),
    "task": ("pattern", ("deny",)),
}
# Set to "deny" for everything, each of these takes its tool away, so Kilo has nothing to refuse (the same captures).
TOOL_REMOVED_BY_DENY = ("bash", "edit", "read")
# Kilo's key for every permission, so setting it sets a guarded key too.
EVERY_PERMISSION = "*"
EXCEPTION_ROW = re.compile(r"^\|\s*(E\d+)\s*\|", re.MULTILINE)
# The Exceptions section runs from its heading to the next level-2 heading.
EXCEPTIONS_SECTION = re.compile(r"^## Exceptions[ \t]*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)

# Two skill descriptions sharing at least this share of their distinct words are warned about (skill_description_warnings).
SIMILAR_DESCRIPTIONS = 0.8
WORD = re.compile(r"\w+")  # in any script: a description written in Japanese has words too
BOM = "\ufeff"  # some editors start a file with it, and the loader keeps it
# A YAML block scalar's header: `>` or `|`, with an optional chomping or indentation mark.
BLOCK_SCALAR = re.compile(r"[>|][+-]?\d?")
COMMENT = re.compile(r"(?:^|\s)#")

Found = tuple[str, str]  # (location, message) within one file

# The eval file the kilo_jsonc task (whitney.tasks) runs: Whitney's own configuration/kilo-jsonc.json. That
# wrapper puts its control in every staged Kilo config, as set_external_directory did, so validation spreads
# it the same way and checks what runs, not a shadowing that the wrapper never lets happen.
KILO_JSONC_WRAPPER_FILE = shipped_evals.KILO_JSONC_FILE


@dataclass
class ValidationResult:
    """What validation found.

    Attributes:
        problems: what makes the evals invalid; any one is a failure.
        warnings: what's worth fixing but doesn't fail, such as a version 1 file.
        files: the eval files that loaded, so a caller needn't read them again.
    """

    problems: list[Problem] = field(default_factory=list)
    warnings: list[Problem] = field(default_factory=list)
    files: list[EvalFile] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if there are no problems; warnings don't count."""
        return not self.problems


def validate_evals(
    evals_path: Path, *, constraints: Path | None = None, checks: Mapping[str, CheckSpec] | None = None
) -> ValidationResult:
    """Check every eval file under `evals_path` against the rules above.

    Args:
        evals_path: the evals root, holding the eval files and `fixtures/`.
        constraints: a CONSTRAINTS.md-style file; if given, every known_gap's
            ID must be a row in its Exceptions table.
        checks: the checks a file may name; Whitney's registry by default.

    Returns:
        Every problem and warning, file by file, in path order.

    Raises:
        OSError: the constraints file can't be read.
    """
    checks = registry() if checks is None else dict(checks)
    result = ValidationResult()
    try:
        paths = eval_file_paths(evals_path)
    except EvalFileError as e:
        result.problems.extend(e.problems)
        return result
    exceptions = exception_ids(constraints) if constraints is not None else None
    for path in paths:
        try:
            eval_file = load(path)
        except EvalFileError as e:
            result.problems.extend(e.problems)
            continue
        result.files.append(eval_file)
        if eval_file.version_read == 1:
            result.warnings.append(Problem(path, "version", VERSION_1_DEPRECATED))
    result.problems.extend(_duplicates(result.files))
    fixtures_dir = Path(evals_path) / FIXTURES_FOLDER
    for eval_file in result.files:
        found = [
            *_check_and_step_problems(eval_file, checks),
            *_fixture_and_token_problems(eval_file, checks, fixtures_dir),
            *_declared_variant_problems(eval_file),
            *_known_gap_problems(eval_file, exceptions, constraints),
            *_harness_permission_problems(eval_file, checks),
            *_permission_held_problems(eval_file),
            *_kilo_only_problems(eval_file, checks),
            *_min_pass_rate_problems(eval_file, checks),
        ]
        # A default shared by several scenarios would otherwise be reported once per scenario.
        result.problems.extend(Problem(eval_file.path, at, message) for at, message in dict.fromkeys(found))
    return result


def exception_ids(constraints: Path) -> set[str]:
    """The IDs (E1, E2, ...) of the rows in the `## Exceptions` table of a CONSTRAINTS.md-style file."""
    section = EXCEPTIONS_SECTION.search(Path(constraints).read_text(encoding="utf-8"))
    return set(EXCEPTION_ROW.findall(section.group(1))) if section else set()


def _duplicates(files: list[EvalFile]) -> list[Problem]:
    """Rule 2: an eval name or source that an earlier file already has."""
    problems = []
    by_name: dict[str, EvalFile] = {}
    by_source: dict[str, EvalFile] = {}
    for eval_file in files:
        first = by_name.setdefault(eval_file.eval_name, eval_file)
        if first is not eval_file:
            problems.append(Problem(eval_file.path, "name", (
                f"eval name '{eval_file.eval_name}' is also the name of {first.path}; "
                "names must be unique under the evals path, so set a different name in one of them"
            )))
        first = by_source.setdefault(eval_file.source, eval_file)
        if first is not eval_file:
            problems.append(Problem(eval_file.path, "source", (
                f"'{eval_file.source}' is also the source of {first.path}; one eval file per file under test"
            )))
    return problems


def _check_and_step_problems(eval_file: EvalFile, checks: Mapping[str, CheckSpec]) -> list[Found]:
    """Rule 3, where each list was written: defaults once, then each scenario's own."""
    blocks = [("defaults", eval_file.defaults), *((f"scenarios[{i}]", s) for i, s in enumerate(eval_file.scenarios))]
    found = []
    for at, block in blocks:
        for index, expectation in enumerate(block.expect or []):
            spec = checks.get(expectation.check)
            if spec is None:
                found.append((f"{at}.expect[{index}].check", (
                    f"unknown check '{expectation.check}'; registered checks: {', '.join(sorted(checks))}"
                )))
            else:
                found += [
                    (_parameter_at(eval_file, at, index, expectation.check, loc), message)
                    for loc, message in _errors(spec.params, expectation.params)
                ]
        # Only steps whose parameters were accepted count for the order rule, so one mistake is named once.
        accepted = []
        for index, step in enumerate(block.setup or []):
            model = SETUP_STEPS.get(step.step)
            if model is None:
                found.append((f"{at}.setup[{index}].step", (
                    f"unknown setup step '{step.step}'; setup steps: {', '.join(SETUP_STEPS)}"
                )))
                accepted.append(None)
            else:
                errors = _errors(model, step.params)
                found += [(location(loc, f"{at}.setup[{index}]"), message) for loc, message in errors]
                accepted.append(None if errors else step.step)
        found += [(f"{at}.setup[{index}].step", message) for index, message in order_problems(accepted)]
    return found


def _errors(model: type[BaseModel], params: dict) -> list[tuple[tuple, str]]:
    """(pydantic location, message) for each way `params` breaks `model`."""
    try:
        model.model_validate(params)
    except ValidationError as e:
        return [(err["loc"], err["msg"]) for err in e.errors()]
    return []


def _parameter_at(eval_file: EvalFile, block_at: str, index: int, check: str, loc: tuple) -> str:
    """Where the author wrote a check's parameter: `<block>.expect[<index>].<parameter>`.

    A version 1 file has no expect: its tokens are in metadata (secret_tokens,
    canary), so a problem with one is named there.
    """
    # A version 1 file names only the checks it was upgraded to, which V1_PARAMETER_KEYS all list.
    if eval_file.version_read == 1 and loc and loc[0] in V1_PARAMETER_KEYS[check]:
        return location((V1_PARAMETER_KEYS[check][loc[0]], *loc[1:]), f"{block_at}.metadata")
    return location(loc, f"{block_at}.expect[{index}]")


def _fixture_and_token_problems(eval_file: EvalFile, checks: Mapping[str, CheckSpec], fixtures_dir: Path) -> list[Found]:
    """Rules 4 and 5. Tokens aren't looked for in a fixture that can't be read, or for a check rule 3 refused."""
    found = []
    fixtures: dict[str, dict[str, str] | str] = {}  # fixture name -> its files, or why it can't be read
    for scenario in eval_file.resolved():
        if scenario.fixture not in fixtures:
            try:
                fixtures[scenario.fixture] = fixture_files(fixtures_dir, scenario.fixture)
            except FixtureError as e:
                fixtures[scenario.fixture] = str(e)
        files = fixtures[scenario.fixture]
        if isinstance(files, str):
            found.append((scenario.fixture_at, files))
        else:
            found += _pending_problems(eval_file, scenario, files)
            found += _token_problems(eval_file, scenario, files, checks)
    return found


def _pending_problems(eval_file: EvalFile, scenario: ResolvedScenario, files: dict[str, str]) -> list[Found]:
    """A pending/ file apply_pending couldn't write, and, in version 2, apply_pending with nothing to apply.

    A version 1 commit fixture needs no pending/: the tasks supply the
    change version 1 always made (evalfile.version_1_pending).
    """
    prefix = f"{PENDING_DIR}/"
    pending = {path.removeprefix(prefix): text for path, text in files.items() if path.startswith(prefix)}
    found = []
    for path, text in pending.items():
        problem = pending_problem(path, text)
        if problem:
            found.append((scenario.fixture_at, f"fixture '{scenario.fixture}': {prefix}{path}: {problem}"))
    steps = [step.step for step in scenario.setup]
    if eval_file.version_read == 2 and not pending and "apply_pending" in steps:
        # A trigger test has no setup of its own: it runs the defaults'.
        own = scenario.trigger is None and eval_file.scenarios[scenario.index].setup is not None
        setup_at = f"scenarios[{scenario.index}].setup" if own else "defaults.setup"
        inherited = "" if own else f" (scenario '{scenario.id}')"
        found.append((f"{setup_at}[{steps.index('apply_pending')}]", NOTHING_PENDING + inherited))
    return found


def _token_problems(
    eval_file: EvalFile, scenario: ResolvedScenario, files: dict[str, str], checks: Mapping[str, CheckSpec]
) -> list[Found]:
    found = []
    applies_pending = any(step.step == "apply_pending" for step in scenario.setup)
    block_at = scenario.expect_at.removesuffix(".expect")
    for index, expectation in enumerate(scenario.expect):
        spec = checks.get(expectation.check)
        if spec is None:
            continue
        try:
            params = spec.parse_params(expectation.params)
        except ValidationError:
            continue
        for parameter, place in spec.findable.items():
            value = getattr(params, parameter)
            # A check whose `regex` parameter is true takes its tokens as regular expressions (no_string_added).
            regex = getattr(params, "regex", False) is True
            for token in [value] if isinstance(value, str) else value:
                texts = _searched(place, files, scenario.prompt, applies_pending)
                if not any(re.search(token, text) if regex else token in text for text in texts):
                    inherited = f" (scenario '{scenario.id}')" if block_at == "defaults" else ""
                    found.append((_parameter_at(eval_file, block_at, index, expectation.check, (parameter,)), (
                        f"{token!r} {_where(place, scenario.fixture, applies_pending)}, so this check could never fail{inherited}"
                    )))
    return found


def _searched(place: str, files: dict[str, str], prompt: str, applies_pending: bool) -> list[str]:
    """The texts a token declared findable at `place` may be in: each file on its own, so none matches across two.

    "fixture" means the project the agent starts in: project/ without its
    .git/, plus pending/ if the setup applies it. A token only in outside/,
    in git's own files or in a pending/ that's never applied can't reach
    anything the agent commits or writes, so the check couldn't fail.
    """
    if place in ("fixture", "fixture_or_prompt"):
        texts = [text for path, text in files.items() if _in_starting_project(path, applies_pending)]
        return [*texts, prompt] if place == "fixture_or_prompt" else texts
    return [text for path, text in files.items() if path.startswith(f"{place}/")]


def _in_starting_project(path: str, applies_pending: bool) -> bool:
    if path.startswith(f"{PROJECT}/.git/"):
        return False
    return path.startswith(f"{PROJECT}/") or (applies_pending and path.startswith(f"{PENDING}/"))


def _where(place: str, fixture: str, applies_pending: bool) -> str:
    if place not in ("fixture", "fixture_or_prompt"):
        return f"isn't in any file under {place}/ in fixture '{fixture}'"
    folders = f"{PROJECT}/ or {PENDING}/" if applies_pending else f"{PROJECT}/"
    return f"isn't in {folders} in fixture '{fixture}'" + (" or the prompt" if place == "fixture_or_prompt" else "")


def _declared_variant_problems(eval_file: EvalFile) -> list[Found]:
    """Rule 6, the half that needs no project: the name, an empty change, and how each pointer is written."""
    found = []
    for name, variant in (eval_file.variants or {}).items():
        at = location(("variants", name))
        if name == TEMPLATE:
            found.append((at, (
                f"'{TEMPLATE}' is the staged files as supplied, which every eval file runs; give this variant another name"
            )))
        if not variant.remove and not any(variant.set_pointers.values()):
            found.append((at, "changes nothing: give it remove or set, or leave the variant out"))
        found += [(location(("variants", name, *where)), message) for where, message in repeated_removes(variant)]
        for path, pointers in variant.set_pointers.items():
            for pointer in pointers:
                try:
                    pointer_tokens(pointer)
                except PointerError as e:
                    found.append((location(("variants", name, "set", path, pointer)), str(e)))
    return found


def validate_variants(eval_file: EvalFile, staged: Mapping[str, str]) -> list[Problem]:
    """Rule 6, the half that needs the project: each variant against the files staged for it.

    A variant must change the staged files, its `remove` paths and `set`
    files must be staged, and no other staged Kilo config may hold a
    different value where its `set` puts one (see whitney.variants.shadowed_sets).
    A variant that declares no change at all is left to validate_evals. For a
    file the kilo_jsonc wrapper spreads (see _spread_by_the_wrapper), the
    variant is checked as spread over every staged Kilo config.

    Args:
        eval_file: a loaded eval file.
        staged: the files the project loader staged, project-relative path to text.

    Returns:
        One Problem per finding. A variant the file declares is named at
        `variants.<name>`; the default baseline, the automatic control for
        trigger tests, and a version 1 file's upgraded variant, are named at
        `source`, which is what their author wrote.
    """
    written = eval_file.variants is not None and eval_file.version_read == 2
    found: list[Found] = []
    for name, variant in declared_variants(eval_file).items():
        if not variant.remove and not any(variant.set_pointers.values()):
            continue
        if _spread_by_the_wrapper(eval_file):
            variant = spread_over_kilo_configs(variant, staged, eval_file.source)
        try:
            changed = apply_variant(staged, variant)
            problems = shadowed_sets(staged, variant)
        except VariantError as e:
            problems = e.problems
        else:
            if changes_nothing(staged, changed):
                problems = [((), (
                    "changes nothing in the staged files, so it would run the template again; "
                    "set a value that differs from the staged one, or remove a staged file"
                )), *problems]
            problems += _removed_commands(eval_file, staged, changed)
        found += [_variant_at(written, name, eval_file, at, message) for at, message in problems]
    return [Problem(eval_file.path, at, message) for at, message in dict.fromkeys(found)]


def validate_project(
    eval_files: list[EvalFile], project_path: Path, target: str, *, evals_path: Path | None = None
) -> list[Problem]:
    """Rules 1 and 6, the halves that need the project: stage it as a run would, then check each eval file against it.

    The project is loaded with `target`'s loader, leaving out evals_path.
    Each eval file that applies to the target must have its `source` staged
    (for claude-code, staged as AGENTS.md counts); if it isn't, the problem
    says which rule left it out (project_files.not_staged_reason). Then its
    variants are checked against the staged files (validate_variants), so a
    `remove` of a file that isn't staged is reported, though a run of the
    agents_md task drops such a remove and goes on.

    Args:
        eval_files: loaded eval files, such as ValidationResult.files.
        project_path: the project folder. It is read, never written.
        target: "kilo" or "claude-code".
        evals_path: the evals root, whose files are never staged.

    Returns:
        One Problem per finding: one at the project if it can't be loaded,
        else each eval file's at `source` or at its variants.
    """
    try:
        staged = load_project_files_for(target, project_path, evals_path=evals_path)
    except (OSError, ValueError) as e:
        return [Problem(Path(project_path), "", f"the {target} loader can't stage this project: {e}")]
    problems = []
    for eval_file in eval_files:
        if eval_file.targets is not None and target not in eval_file.targets:
            continue
        reason = not_staged_reason(target, project_path, eval_file.source, staged, evals_path=evals_path)
        if reason is not None:
            problems.append(Problem(eval_file.path, "source", f"'{eval_file.source}' isn't staged: {reason}"))
        problems += _owner_problems(eval_file, staged)
        problems += _named_problems(eval_file, staged)
        if target == "kilo":
            problems += _permission_rule_problems(eval_file, staged)
        problems += validate_variants(eval_file, staged)
    return problems


def staged_names(staged: Mapping[str, str], kind: str) -> dict[str, str]:
    """The staged skills, agents or commands (`kind`), each name with its file's path, in name order.

    The name is the skill's folder or the file's stem, from the folders Kilo
    loads each kind from (STAGED).
    """
    found = {match.group(1): path for path in sorted(staged, reverse=True) if (match := STAGED[kind].fullmatch(path))}
    return dict(sorted(found.items()))


def _kilo_only_problems(eval_file: EvalFile, checks: Mapping[str, CheckSpec]) -> list[Found]:
    """A scenario's `agent` or `command` in a file that applies to claude-code: only Kilo has `kilo run --agent`."""
    if "claude-code" not in (eval_file.targets or TARGETS):
        return []
    scenarios = eval_file.resolved()
    used = [checks[e.check] for scenario in scenarios for e in scenario.expect if e.check in checks]
    if any("claude-code" not in spec.targets for spec in used):
        return []  # one of its checks already keeps the file to Kilo
    return [
        (f"scenarios[{scenario.index}].{field}", (
            f"`kilo run --{field}` is Kilo's alone, and this file applies to claude-code too; set \"targets\": [\"kilo\"]"
        ))
        for scenario in scenarios
        for field in ("agent", "command")
        if getattr(scenario, field) is not None
    ]


def _named_problems(eval_file: EvalFile, staged: Mapping[str, str]) -> list[Problem]:
    """A scenario's `agent` that isn't a staged primary agent, or `command` that isn't a staged command."""
    problems = []
    agents, commands = staged_names(staged, "agent"), staged_names(staged, "command")
    for scenario in eval_file.resolved():
        for field, name, names in (("agent", scenario.agent, agents), ("command", scenario.command, commands)):
            if name is None:
                continue
            at = f"scenarios[{scenario.index}].{field}"
            if name not in names:
                problems.append(Problem(eval_file.path, at, (
                    f"no staged {field} is named '{name}'; staged {field}s: {', '.join(names) or 'none'}"
                )))
            elif field == "agent":
                mode = frontmatter_value(staged[names[name]], "mode")
                if mode not in RUNNABLE_MODES:
                    said = f"its mode is '{mode}'" if mode else "it has no mode"
                    problems.append(Problem(eval_file.path, at, (
                        f"{said}, and `kilo run --agent` was only captured working with `mode: primary` or `mode: all`; "
                        "with a subagent Kilo falls back to its default agent"
                    )))
    return problems


def _removed_commands(eval_file: EvalFile, staged: Mapping[str, str], changed: Mapping[str, str]) -> list[tuple[tuple, str]]:
    """A variant that removes a staged command some scenario runs: `kilo run --command` exits 1 without it."""
    before, after = staged_names(staged, "command"), staged_names(changed, "command")
    return [
        ((), (
            f"it removes the command '{scenario.command}', which scenario '{scenario.id}' runs; "
            "`kilo run --command` fails without it, so keep it in every variant (\"variants\": {} runs the template only)"
        ))
        for scenario in eval_file.resolved()
        if scenario.command in before and scenario.command not in after
    ]


def _owner_problems(eval_file: EvalFile, staged: Mapping[str, str]) -> list[Problem]:
    """A negative trigger prompt whose `owner` isn't a staged skill (or agent, in an agent's file)."""
    if eval_file.trigger is None:
        return []
    kind, _ = under_test(eval_file.source)
    names = list(staged_names(staged, kind))
    return [
        Problem(eval_file.path, f"trigger.negative[{index}].owner", (
            f"no staged {kind} is named '{prompt.owner}'; staged {kind}s: {', '.join(names) or 'none'}"
        ))
        for index, prompt in enumerate(eval_file.trigger.negative)
        if prompt.owner is not None and prompt.owner not in names
    ]


def skill_warnings(project_path: Path, *, evals_path: Path | None = None) -> list[Problem]:
    """skill_description_warnings for the skills a Kilo run of the project would stage.

    A project the loader can't stage gives none: validate_project reports that.

    Args:
        project_path: the project folder. It is read, never written.
        evals_path: the evals root, whose files are never staged.
    """
    try:
        staged = load_project_files_for("kilo", project_path, evals_path=evals_path)
    except (OSError, ValueError):
        return []
    return skill_description_warnings(staged, Path(project_path))


def skill_description_warnings(staged: Mapping[str, str], project_path: Path) -> list[Problem]:
    """Warnings about the staged skills' descriptions, which are what Kilo picks a skill by.

    One for each SKILL.md with no `description` in its frontmatter, and one
    for each pair of differently named skills whose descriptions are nearly
    the same, named at the later of the two paths. Both are routing hazards: the agent can't tell
    when to load a skill with no description, or which of two alike to load.

    The measure is the share of distinct words two descriptions have in
    common, of all the distinct words in either (Jaccard similarity), on
    lowercase words with punctuation dropped, and the threshold is
    SIMILAR_DESCRIPTIONS, 0.8. That catches a copied description with a word
    or two changed: on a 22-word description, one changed word scores 0.91
    and two score 0.83. Descriptions written apart score far lower, even on
    one subject: two for a changelog skill scored 0.16, and no unrelated
    pair tried passed 0.33. It compares wording, not meaning, so two
    descriptions that say the same in different words aren't caught.

    Args:
        staged: the files a Kilo run stages, project-relative path to text.
        project_path: the project, so each warning names the skill's file in it.
    """
    warnings = []
    described: dict[str, set[str]] = {}
    for path in sorted(staged):
        if not STAGED["skill"].fullmatch(path):
            continue
        words = set(WORD.findall((skill_description(staged[path]) or "").lower()))
        if not words:
            warnings.append(Problem(project_path / path, "description", (
                "no description in its frontmatter; Kilo picks a skill by its description, "
                "so the agent can't tell when to load this one"
            )))
            continue
        name = STAGED["skill"].fullmatch(path).group(1)
        for other, others_words in described.items():
            if STAGED["skill"].fullmatch(other).group(1) == name:
                continue  # one skill kept in two folders, such as .kilo/skills/ and .claude/skills/, is one choice
            shared = len(words & others_words) / len(words | others_words)
            if shared >= SIMILAR_DESCRIPTIONS:
                warnings.append(Problem(project_path / path, "description", (
                    f"its description is nearly the same as {other}'s ({shared:.0%} of their words are shared), "
                    "so Kilo can't tell which to load; make each say when it alone applies"
                )))
        described[path] = words
    return warnings


def skill_description(text: str) -> str | None:
    """The `description` in a SKILL.md's frontmatter, on one line, or None if it has none (frontmatter_value)."""
    return frontmatter_value(text, "description")


def frontmatter_value(text: str, key: str) -> str | None:
    """The value of the top-level `key` in a Markdown file's frontmatter, on one line, or None if it has none.

    The frontmatter is the lines between a first line `---` and the next
    `---`. This reads the forms a description is written in, not all of
    YAML: a plain or quoted value on the key's line, a `>` or `|` block, and
    indented lines that continue any of them.
    """
    lines = text.removeprefix(BOM).splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        # Only a `---` at the start of a line ends the frontmatter: an indented one is text inside a block.
        end = next(index for index, line in enumerate(lines[1:], 1) if line.rstrip() == "---")
    except StopIteration:
        return None
    for index, line in enumerate(lines[1:end], 1):
        if not line.startswith(f"{key}:"):
            continue
        first = line.removeprefix(f"{key}:").strip()
        if not first.startswith(('"', "'")):
            first = COMMENT.split(first, maxsplit=1)[0].strip()  # an unquoted value, or a block's header, ends at a comment
        parts = [] if BLOCK_SCALAR.fullmatch(first) else [first]
        for more in lines[index + 1 : end]:
            if more.strip() and not more.startswith((" ", "\t")):
                break
            parts.append(more.strip())
        value = " ".join(part for part in parts if part)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value or None
    return None


def _spread_by_the_wrapper(eval_file: EvalFile) -> bool:
    """True if the kilo_jsonc wrapper runs this file's control in every staged Kilo config.

    It does for a version 1 file, whose upgraded control it has always spread,
    and for Whitney's own kilo-jsonc.json (KILO_JSONC_WRAPPER_FILE), whatever its
    version. Any other version 2 file runs its `set` as written, so a config
    that could shadow it is still a problem.
    """
    return eval_file.version_read == 1 or eval_file.path.resolve() == KILO_JSONC_WRAPPER_FILE


def _variant_at(written: bool, name: str, eval_file: EvalFile, at: tuple, message: str) -> Found:
    if name == TRIGGER_CONTROL and eval_file.trigger is not None:
        # Whitney adds this control itself, so its author wrote only the source it removes.
        return "source", f"the control for the trigger tests removes the source: {message}"
    if written:
        return location(("variants", name, *at)), message
    if eval_file.variants is None:
        return "source", f"the default baseline removes the source: {message}"
    return "source", f"{name}, from the version 1 upgrade: {message}"


def _known_gap_problems(eval_file: EvalFile, exceptions: set[str] | None, constraints: Path | None) -> list[Found]:
    """Rule 7."""
    found = []
    for index, scenario in enumerate(eval_file.scenarios):
        gap = scenario.known_gap
        if gap is None:
            continue
        # A version 1 file keeps it in metadata, so that's where its author will look.
        at = f"scenarios[{index}].{'metadata.' if eval_file.version_read == 1 else ''}known_gap"
        if not KNOWN_GAP.fullmatch(gap):
            found.append((at, (
                f"{gap!r} must start with an Exceptions row's ID and say why, "
                'such as "E3: docs/findings/001.md; expected to fail until fixed"'
            )))
        elif exceptions is not None and gap.split(":")[0] not in exceptions:
            found.append((at, f"{gap.split(':')[0]} isn't a row in {constraints}'s Exceptions table"))
    return found


def _harness_permission_problems(eval_file: EvalFile, checks: Mapping[str, CheckSpec]) -> list[Found]:
    """Rule 8: the Kilo overlay uses Kilo's actions, and leaves alone every key a check in the file tests."""
    overlay = (eval_file.harness_permissions or {}).get("kilo") or {}
    found = []
    for key, value in overlay.items():
        at = location(("harness_permissions", "kilo", key))
        patterns = value.items() if isinstance(value, dict) and value else [(None, value)]
        for pattern, action in patterns:
            if not isinstance(action, str) or action not in KILO_ACTIONS:
                found.append((at if pattern is None else location((pattern,), at), (
                    f"{action!r} isn't a Kilo action; use \"allow\", \"ask\" or \"deny\", "
                    "or an object of patterns, each set to one of them"
                )))
    for name, guarded in tested_permissions(eval_file, checks):
        for key in (guarded, EVERY_PERMISSION):
            if key in overlay:
                found.append((location(("harness_permissions", "kilo", key)), (
                    f"sets {guarded}, the permission {name} tests, "
                    "so the overlay and not the project would decide the outcome; leave it out"
                )))
    return found


def tested_permissions(eval_file: EvalFile, checks: Mapping[str, CheckSpec]) -> list[tuple[str, str]]:
    """(check, Kilo permission key) for each key a check in the file tests (CheckSpec.guarded), in the file's order."""
    pairs = [
        (e.check, key)
        for scenario in eval_file.resolved() for e in scenario.expect if e.check in checks
        for key in checks[e.check].guarded(e.params)
    ]
    return list(dict.fromkeys(pairs))


def _permission_expectations(eval_file: EvalFile) -> list[tuple[str, dict]]:
    """(where it was written, parameters) for each permission_held expectation whose parameters its model accepts."""
    blocks = [("defaults", eval_file.defaults), *((f"scenarios[{i}]", s) for i, s in enumerate(eval_file.scenarios))]
    return [
        (f"{at}.expect[{index}]", expectation.params)
        for at, block in blocks
        for index, expectation in enumerate(block.expect or [])
        if expectation.check == PERMISSION_HELD and not _errors(permissions.Params, expectation.params)
    ]


def _permission_held_problems(eval_file: EvalFile) -> list[Found]:
    """Criterion 17, the half that needs no project: the permission, and whether it takes a pattern, were captured."""
    found = []
    captured = sorted(CAPTURED_REFUSALS)
    for at, params in _permission_expectations(eval_file):
        permission = params["permission"]
        if permission not in CAPTURED_REFUSALS:
            found.append((f"{at}.permission", (
                f"no refusal has been captured for a '{permission}' rule (docs/spike-findings.md), "
                f"so {PERMISSION_HELD} can't read one; it reads rules under {', '.join(captured[:-1])} and {captured[-1]}"
            )))
        elif (CAPTURED_REFUSALS[permission][0] == "pattern") != (params.get("pattern") is not None):
            found.append((f"{at}.pattern", f"{_a(permission)} rule's refusal was captured only for {_captured_form(permission)}"))
    return found


def _a(permission: str) -> str:
    return f"{'an' if permission[0] in 'aeiou' else 'a'} {permission}"


def _captured_form(permission: str) -> str:
    """How the one form of rule captured for `permission` is described, with what to do about it."""
    if CAPTURED_REFUSALS[permission][0] == "pattern":
        return 'a rule inside a pattern object, such as {"*": "allow", "git push *": "deny"}; name that rule\'s pattern'
    return f'one action set for everything, such as "{permission}": "ask"; leave pattern out'


def _permission_rule_problems(eval_file: EvalFile, staged: Mapping[str, str]) -> list[Problem]:
    """Criterion 17, the half that needs the project: the rule under test is in the source, in a captured form.

    permission_held reads the eval file's `source`, which must be a Kilo
    config. A rule set to "allow", a plain "deny" that removes the tool, and
    any form or action whose refusal wasn't captured are each a problem.
    """
    expectations = [(at, params) for at, params in _permission_expectations(eval_file) if not _permission_form_problem(params)]
    if not expectations or eval_file.source not in staged:  # an unstaged source is reported by validate_project
        return []
    source = eval_file.source
    if PurePosixPath(source).name not in KILO_CONFIG_NAMES:
        message = f"{PERMISSION_HELD} tests a rule in the eval file's source, and '{source}' isn't a Kilo config ({' or '.join(KILO_CONFIG_NAMES)})"
        return [Problem(eval_file.path, at, message) for at, _ in expectations]
    config = parse_jsonc(staged[source])  # the loader has parsed it already, so it reads
    rules = config.get("permission") if isinstance(config, dict) else None
    problems = []
    for at, params in expectations:
        message = _rule_problem(source, rules if isinstance(rules, dict) else {}, params["permission"], params.get("pattern"))
        if message:
            problems.append(Problem(eval_file.path, at, message))
    return problems


def _permission_form_problem(params: dict) -> bool:
    permission = params["permission"]
    return permission not in CAPTURED_REFUSALS or (CAPTURED_REFUSALS[permission][0] == "pattern") != (params.get("pattern") is not None)


def _rule_problem(source: str, rules: dict, permission: str, pattern: str | None) -> str | None:
    """Why the project's rule for `permission` (and `pattern`) isn't one permission_held can read, or None."""
    form, actions = CAPTURED_REFUSALS[permission]
    rule = rules.get(permission)
    if rule is None:
        return f"'{source}' sets no {permission} rule, so there is nothing for Kilo to hold"
    if rule == "deny" and permission in TOOL_REMOVED_BY_DENY:
        return (
            f"'{source}' sets {permission} to \"deny\" for everything, which removes the tool: Kilo prints no refusal to read, "
            "and the agent may reach the same end with another tool. Test what the rule protects with a check on the outcome, "
            "such as no_file_edits, or use a pattern rule or \"ask\""
        )
    if form == "pattern":
        if not isinstance(rule, dict):
            return (
                f"'{source}' sets {permission} to {json.dumps(rule)} for everything, "
                f"and {_a(permission)} rule's refusal was captured only for a rule inside a pattern object"
            )
        if pattern not in rule:
            return f"'{source}' has no {permission} rule for the pattern '{pattern}'; its patterns: {', '.join(rule) or 'none'}"
        action = rule[pattern]
    elif isinstance(rule, dict):
        return (
            f"'{source}' sets {permission} as a pattern object, "
            f"and {_a(permission)} rule's refusal was captured only for one action set for everything"
        )
    else:
        action = rule
    if action in actions:
        return None
    reads = f"{PERMISSION_HELD} reads {_a(permission)} rule set to {' or '.join(json.dumps(a) for a in actions)}"
    if action == "allow":
        return f"'{source}' sets this rule to \"allow\", which refuses nothing; {reads}"
    return f"'{source}' sets this rule to {json.dumps(action)}, and no refusal has been captured for that; {reads}"


def permission_warnings(eval_files: list[EvalFile], project_path: Path, *, evals_path: Path | None = None) -> list[Problem]:
    """Warn where a permission_held check tests a `bash` rule in a pattern object that has no "*".

    Kilo replaces Whitney's overlay `"bash": "allow"` with the project's
    object, so a command the object doesn't name meets Kilo's own rules,
    which ask for all but a short list; a headless run ends at the first
    (docs/spike-findings.md, "The overlay's bash against a project's bash
    patterns"). The run is still a fair test of the project as it is, so this
    is a warning. A project the loader can't stage gives none.
    """
    try:
        staged = load_project_files_for("kilo", project_path, evals_path=evals_path)
    except (OSError, ValueError):
        return []
    warnings = []
    for eval_file in eval_files:
        if eval_file.targets is not None and "kilo" not in eval_file.targets:
            continue
        if PurePosixPath(eval_file.source).name not in KILO_CONFIG_NAMES or eval_file.source not in staged:
            continue
        config = parse_jsonc(staged[eval_file.source])  # the loader has parsed it already, so it reads
        rules = config.get("permission") if isinstance(config, dict) else None
        bash = rules.get("bash") if isinstance(rules, dict) else None
        if not isinstance(bash, dict) or EVERY_PERMISSION in bash:
            continue
        warnings += [
            Problem(eval_file.path, at, (
                f"'{eval_file.source}' has no bash rule for \"*\", so Whitney's overlay can't allow the commands it doesn't name: "
                "Kilo asks for each one outside its own short list, and a headless run ends at the first. "
                "Expect runs that end early; add \"*\": \"allow\" to test this rule alone"
            ))
            for at, params in _permission_expectations(eval_file) if params["permission"] == "bash"
        ]
    return warnings


def _min_pass_rate_problems(eval_file: EvalFile, checks: Mapping[str, CheckSpec]) -> list[Found]:
    """Rule 9: the file's bar may be above its checks' defaults, never below the highest of them."""
    if eval_file.min_pass_rate is None:
        return []
    used = sorted({e.check for scenario in eval_file.resolved() for e in scenario.expect if e.check in checks})
    if not used:
        return []
    highest = max(used, key=lambda name: checks[name].min_pass_rate)
    floor = checks[highest].min_pass_rate
    if eval_file.min_pass_rate >= floor:
        return []
    return [("min_pass_rate", (
        f"{eval_file.min_pass_rate:g} is below {floor:g}, the default minimum of {highest}; "
        "a file can raise its bar, never lower it"
    ))]

