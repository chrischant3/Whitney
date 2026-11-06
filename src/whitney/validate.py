"""Validation of eval files that needs only the evals path: no project, no model, no harness.

Rules, numbered as in docs/specs/package-and-expected-results.md ("Validation"):

1. The evals path is a folder (the project half waits for the project loader).
2. Every eval file matches the schema (version 1 files are upgraded first),
   and eval names and `source` values are unique.
3. Every check and setup step is known, and its parameters are valid.
4. Every fixture is a folder in `fixtures/`, holds no symlinks and is UTF-8 text.
5. Every token parameter is findable where its check says, so the check can fail.
7. A `known_gap` starts with an Exceptions ID (`E3: why`); with a constraints
   file, that ID is a row in its Exceptions table.
9. A file's `min_pass_rate` isn't below the default minimum of any check it uses.

Each problem names the file and the location, such as
`scenarios[1].expect[0].strings`, so it can be fixed without reading code.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from whitney.checks import CheckSpec, registry
from whitney.evalfile import (
    FIXTURES_FOLDER,
    SETUP_STEPS,
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
)

KNOWN_GAP = re.compile(r"E\d+: .+")
EXCEPTION_ROW = re.compile(r"^\|\s*(E\d+)\s*\|", re.MULTILINE)
# The Exceptions section runs from its heading to the next level-2 heading.
EXCEPTIONS_SECTION = re.compile(r"^## Exceptions[ \t]*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)

Found = tuple[str, str]  # (location, message) within one file


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
            *_known_gap_problems(eval_file, exceptions, constraints),
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
                found += _parameter_problems(spec.params, expectation.params, f"{at}.expect[{index}]")
        for index, step in enumerate(block.setup or []):
            model = SETUP_STEPS.get(step.step)
            if model is None:
                found.append((f"{at}.setup[{index}].step", (
                    f"unknown setup step '{step.step}'; setup steps: {', '.join(SETUP_STEPS)}"
                )))
            else:
                found += _parameter_problems(model, step.params, f"{at}.setup[{index}]")
    return found


def _parameter_problems(model: type[BaseModel], params: dict, at: str) -> list[Found]:
    try:
        model.model_validate(params)
    except ValidationError as e:
        return [(location(err["loc"], at), err["msg"]) for err in e.errors()]
    return []


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
            found += _token_problems(scenario, files, checks)
    return found


def _token_problems(scenario: ResolvedScenario, files: dict[str, str], checks: Mapping[str, CheckSpec]) -> list[Found]:
    found = []
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
            for token in [value] if isinstance(value, str) else value:
                if not any(token in text for text in _searched(place, files, scenario.prompt)):
                    inherited = f" (scenario '{scenario.id}')" if scenario.expect_at.startswith("defaults") else ""
                    found.append((f"{scenario.expect_at}[{index}].{parameter}", (
                        f"{token!r} {_where(place, scenario.fixture)}, so this check could never fail{inherited}"
                    )))
    return found


def _searched(place: str, files: dict[str, str], prompt: str) -> list[str]:
    """The texts a token declared findable at `place` may be in: each file on its own, so none matches across two."""
    if place == "fixture":
        return list(files.values())
    if place == "fixture_or_prompt":
        return [*files.values(), prompt]
    return [text for path, text in files.items() if path.startswith(f"{place}/")]


def _where(place: str, fixture: str) -> str:
    if place == "fixture":
        return f"isn't in fixture '{fixture}'"
    if place == "fixture_or_prompt":
        return f"isn't in fixture '{fixture}' or the prompt"
    return f"isn't in any file under {place}/ in fixture '{fixture}'"


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

