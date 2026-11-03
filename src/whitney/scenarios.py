"""Load scenario JSON files into Inspect samples.

One scenario file per instruction source (e.g. `evals/agents-md.json` for
AGENTS.md, `evals/configuration/kilo-jsonc.json` for kilo.jsonc):

    {
      "source": "AGENTS.md",
      "scenarios": [
        {
          "id": "commit-all-neutral",
          "prompt": "Commit all my changes.",
          "fixture": "repo-with-env",
          "pressure": "none",
          "metadata": {"any": "extra fields the scorer needs"}
        }
      ]
    }

`fixture` names a folder under `fixtures/` next to the scenario file's
`evals/` root. The folder mirrors the sandbox working directory: `project/`
is where Kilo runs, and siblings such as `outside/` sit beyond the project.
"""

import json
from pathlib import Path

from inspect_ai.dataset import Sample
from pydantic import BaseModel, Field, ValidationError, field_validator

SANDBOX_ROOT = "."


class ScenarioError(ValueError):
    """A scenario file is malformed. The message names the file and the offending field."""


class Scenario(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    prompt: str
    # A folder name in fixtures/, never a path: an absolute or climbing name would stage host files.
    fixture: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    pressure: str = "none"
    metadata: dict = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def prompt_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ScenarioFile(BaseModel):
    source: str
    scenarios: list[Scenario] = Field(min_length=1)


def load_scenarios(path: Path, fixtures_dir: Path | None = None) -> list[Sample]:
    """Validate a scenario file and turn each scenario into an Inspect Sample.

    Args:
        path: the scenario JSON file.
        fixtures_dir: where fixture folders live. Defaults to `fixtures/` in
            the evals root, which is the folder holding the scenario file or,
            for files in a subfolder such as `configuration/`, its parent.

    Returns:
        Samples with `id` and `input` from the scenario, metadata holding
        `source`, `fixture`, `pressure` and any extra `metadata`, and `files`
        mapping each fixture file to its path under /workspace.

    Raises:
        ScenarioError: invalid JSON, a missing or invalid field, an unknown
            fixture, a fixture folder that resolves outside fixtures_dir or
            holds a symlink, or a duplicate scenario id.
    """
    path = Path(path)
    fixtures_dir = Path(fixtures_dir) if fixtures_dir else _default_fixtures_dir(path)
    parsed = _parse(path)

    seen: set[str] = set()
    samples = []
    for scenario in parsed.scenarios:
        if scenario.id in seen:
            raise ScenarioError(f"{path}: duplicate scenario id '{scenario.id}'")
        seen.add(scenario.id)
        samples.append(
            Sample(
                id=scenario.id,
                input=scenario.prompt,
                metadata={
                    **scenario.metadata,
                    "source": parsed.source,
                    "fixture": scenario.fixture,
                    "pressure": scenario.pressure,
                },
                files=_fixture_files(path, fixtures_dir, scenario.fixture),
            )
        )
    return samples


def _parse(path: Path) -> ScenarioFile:
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ScenarioError(f"{path}: not valid JSON ({e})") from e
    try:
        return ScenarioFile.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(f"{_location(err['loc'])}: {err['msg']}" for err in e.errors())
        raise ScenarioError(f"{path}: {problems}") from e


def _location(loc: tuple) -> str:
    """('scenarios', 1, 'prompt') -> 'scenarios[1].prompt'"""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _default_fixtures_dir(path: Path) -> Path:
    candidates = [folder / "fixtures" for folder in (path.parent, path.parent.parent)]
    return next((folder for folder in candidates if folder.is_dir()), candidates[0])


def _fixture_files(path: Path, fixtures_dir: Path, name: str) -> dict[str, str]:
    folder = fixtures_dir / name
    if not folder.is_dir():
        raise ScenarioError(f"{path}: fixture '{name}' not found in {fixtures_dir}")
    if not folder.resolve().is_relative_to(fixtures_dir.resolve()):
        raise ScenarioError(f"{path}: fixture '{name}' resolves outside {fixtures_dir}")
    files = {}
    for file in sorted(folder.rglob("*")):
        if file.is_symlink():  # it would stage whatever host file it points to
            raise ScenarioError(f"{path}: fixture '{name}' holds a symlink, {file.relative_to(folder)}; use plain files")
        if file.is_file():
            files[(Path(SANDBOX_ROOT) / file.relative_to(folder)).as_posix()] = file.read_text()
    return files
