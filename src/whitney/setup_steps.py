"""Setup steps: what a scenario's `setup` does to the staged fixture before the agent starts.

A scenario's setup is a list of steps, run in order in the sandbox:

    [{"step": "git_init"},
     {"step": "git_commit", "files": ["README.md", "app.py"], "message": "Initial version"},
     {"step": "apply_pending"}]

- `git_init`: an empty repository in project/, which also bounds where Kilo looks for config.
- `git_commit`: commits `files` (paths relative to project/, "." for everything)
  with `message`. Each one adds a commit to `initial_commits`.
- `apply_pending`: writes the fixture's pending/ files over project/, so
  "commit my work" has a real change to commit.

The steps become one shell script (Sample.setup). It runs git with none of the
user's config, hooks or inherited GIT_* variables (whitney.git_env), stops git
looking for a repository above the sandbox, creates the repository with
`--template=` so the user's template hooks aren't copied in, and runs under
`set -e`, so a step that can't complete fails the sample instead of leaving
`initial_commits` or the starting project wrong. A git_commit must come after
a git_init, so it can only commit into the sandbox's own repository.

pending/ is never staged as a folder: `split_pending` takes it out of a
sample's files, and `apply_pending` writes its contents from inside the
script. The agent sees the change, never the folder it came from.

This module is the one place that knows the steps, their parameters and the
rules for their order and pending files. whitney.validate checks an eval file
against SETUP_STEPS, order_problems and pending_problem before anything runs;
build applies the same rules, so a file that skipped validation still can't run.
"""

import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from whitney.git_env import ISOLATED_GIT_SHELL

# Where the agent works, relative to the sandbox root. The same as whitney.agent.PROJECT_DIR, which
# isn't imported because whitney.agent pulls in Inspect's agent machinery and evalfile imports this module.
PROJECT_DIR = "project"
PENDING_DIR = "pending"
NOTHING_PENDING = (
    f"apply_pending has nothing to apply: add {PENDING_DIR}/<file> holding the change to apply after the commit, "
    "or remove apply_pending from setup"
)
COMMIT_BEFORE_INIT = (
    "git_commit before any git_init would commit into whatever repository holds the sandbox; "
    'put {"step": "git_init"} first'
)
# Runs in the sandbox root. GIT_CEILING_DIRECTORIES is set after ISOLATED_GIT_SHELL, which unsets every
# inherited GIT_* variable; it stops git in project/ climbing to the root or above, as isolated_git_env
# does for the agent with the project's parent.
PREAMBLE = f'set -e\n{ISOLATED_GIT_SHELL}export GIT_CEILING_DIRECTORIES="$PWD"\ncd {PROJECT_DIR}\n'


class SetupError(ValueError):
    """A scenario's setup can't be turned into a script: an unknown step, a bad parameter, or nothing to apply."""


class SetupStep(BaseModel):
    """One entry in `setup`: the step's name, and its parameters as the other fields."""

    model_config = ConfigDict(extra="allow")

    step: str

    @property
    def params(self) -> dict[str, Any]:
        """The step's parameters: every field except `step`."""
        return dict(self.model_extra or {})


class _NoStepParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GitCommitParams(BaseModel):
    """git_commit's parameters: the paths to commit, relative to project/ ("." for everything), and the message."""

    model_config = ConfigDict(extra="forbid")

    files: list[Annotated[str, StringConstraints(min_length=1)]] = Field(min_length=1)
    message: str = Field("Initial version", min_length=1)


@dataclass(frozen=True)
class Step:
    """A registered setup step.

    Attributes:
        name: what a scenario writes in `step`.
        params: the pydantic model its parameters must match.
        commits: how many commits it adds to initial_commits.
        script: its shell lines, given its validated parameters and the
            fixture's pending files (path under pending/ to text). It runs
            in project/, after the steps before it.
    """

    name: str
    params: type[BaseModel]
    commits: int
    script: Callable[[Any, Mapping[str, str]], str]


@dataclass(frozen=True)
class SetupScript:
    """A scenario's setup, ready for a Sample.

    Attributes:
        script: the shell script for Sample.setup; None when there are no steps.
        initial_commits: the commits the script makes before the agent starts.
    """

    script: str | None
    initial_commits: int


def _git_init(params: _NoStepParams, pending: Mapping[str, str]) -> str:
    return "git init -q --template=\n"


def _git_commit(params: GitCommitParams, pending: Mapping[str, str]) -> str:
    # `--` so a path that starts with "-" is a path, not an option.
    paths = " ".join(shlex.quote(path) for path in params.files)
    return f"git add -- {paths}\ngit commit -qm {shlex.quote(params.message)}\n"


def _apply_pending(params: _NoStepParams, pending: Mapping[str, str]) -> str:
    if not pending:
        raise SetupError(NOTHING_PENDING)
    lines = []
    for path, text in sorted(pending.items()):
        problem = pending_problem(path, text)
        if problem:
            raise SetupError(f"{PENDING_DIR}/{path}: {problem}")
        # For a file at the top this is `mkdir -p -- .`, which does nothing, so no branch is needed to skip it.
        lines.append(f"mkdir -p -- {shlex.quote(PurePosixPath(path).parent.as_posix())}")
        # printf '%s' writes the text as it was read, where echo or a heredoc would add or reinterpret
        # characters. As it was read is not the bytes on disk: read_text turns CRLF and a lone CR into
        # LF, and bash can't hold a NUL, which is why pending_problem refuses one.
        lines.append(f"printf '%s' {shlex.quote(text)} > {shlex.quote(path)}")
    return "\n".join(lines) + "\n"


STEPS: dict[str, Step] = {
    step.name: step
    for step in (
        Step("git_init", _NoStepParams, commits=0, script=_git_init),
        Step("git_commit", GitCommitParams, commits=1, script=_git_commit),
        Step("apply_pending", _NoStepParams, commits=0, script=_apply_pending),
    )
}
# Each step's parameter model, which whitney.validate checks a file's steps against.
SETUP_STEPS: dict[str, type[BaseModel]] = {name: step.params for name, step in STEPS.items()}


def order_problems(names: Iterable[str]) -> list[tuple[int, str]]:
    """(index, message) for each git_commit that comes before any git_init in a setup's step names."""
    problems, initialised = [], False
    for index, name in enumerate(names):
        initialised = initialised or name == "git_init"
        if name == "git_commit" and not initialised:
            problems.append((index, COMMIT_BEFORE_INIT))
    return problems


def pending_problem(path: str, text: str) -> str | None:
    """Why the pending file at `path` (inside pending/) can't be applied, or None if it can.

    Refused: a path that is absolute or climbs out of project/ with "..",
    anything under .git/ (applied after `git init --template=`, it could plant
    hooks or config), and text holding a NUL, which bash drops.
    """
    parts = PurePosixPath(path).parts
    if PurePosixPath(path).is_absolute() or ".." in parts:
        return f"{PENDING_DIR}/ paths must stay inside {PROJECT_DIR}/"
    if parts[:1] == (".git",):
        return f"a {PENDING_DIR}/.git/ file would change the repository setup made"
    if "\0" in text:
        return "holds a NUL character, which the setup script can't write"
    return None


def build(steps: Iterable[SetupStep], pending: Mapping[str, str]) -> SetupScript:
    """The script that runs `steps` in a sandbox holding the staged fixture, and the commits it makes.

    Args:
        steps: the scenario's resolved setup, in order.
        pending: the fixture's pending/ files, keyed by their path inside
            pending/ (app.py), as split_pending returns them.

    Returns:
        A SetupScript; its script is None when `steps` is empty.

    Raises:
        SetupError: an unknown step, parameters its model refuses, a
            git_commit before any git_init, apply_pending with no pending
            files, or a pending file pending_problem refuses.
    """
    steps = list(steps)
    for index, message in order_problems(call.step for call in steps):
        raise SetupError(f"setup[{index}]: {message}")
    body, commits = [], 0
    for index, call in enumerate(steps):
        step = STEPS.get(call.step)
        if step is None:
            raise SetupError(f"setup[{index}]: unknown setup step '{call.step}'; setup steps: {', '.join(STEPS)}")
        try:
            params = step.params.model_validate(call.params)
        except ValidationError as e:
            raise SetupError(f"setup[{index}]: {call.step}: {e}") from e
        try:
            body.append(step.script(params, pending))
        except SetupError as e:
            raise SetupError(f"setup[{index}]: {e}") from e
        commits += step.commits
    if not body:
        return SetupScript(script=None, initial_commits=0)
    return SetupScript(script=PREAMBLE + "".join(body), initial_commits=commits)


def split_pending(files: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """A sample's files without pending/, and pending/'s files keyed by their path inside it.

    Args:
        files: Sample.files as whitney.scenarios.load_scenarios gives them,
            keyed by their path in the sandbox (project/app.py, pending/app.py).

    Returns:
        (the files to stage, the pending files for build).
    """
    prefix = f"{PENDING_DIR}/"
    staged = {path: text for path, text in files.items() if not path.startswith(prefix)}
    pending = {path.removeprefix(prefix): text for path, text in files.items() if path.startswith(prefix)}
    return staged, pending
