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
user's config, hooks or inherited GIT_* variables (whitney.git_env), creates
the repository with `--template=` so the user's template hooks aren't copied
in, and runs under `set -e`, so a step that can't complete fails the sample
instead of leaving `initial_commits` or the starting project wrong.

pending/ is never staged as a folder: `split_pending` takes it out of a
sample's files, and `apply_pending` writes its contents from inside the
script. The agent sees the change, never the folder it came from.

This module is the one place that knows the steps and their parameters;
whitney.evalfile and whitney.validate check a file's steps against STEPS.
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
    message: str = "Initial version"


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
        raise SetupError(f"apply_pending: the fixture has no {PENDING_DIR}/ files to apply")
    lines = []
    for path, text in sorted(pending.items()):
        # For a file at the top this is `mkdir -p -- .`, which does nothing, so no branch is needed to skip it.
        lines.append(f"mkdir -p -- {shlex.quote(PurePosixPath(path).parent.as_posix())}")
        # printf '%s' writes the text byte for byte; echo or a heredoc would add or reinterpret characters.
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


def build(steps: Iterable[SetupStep], pending: Mapping[str, str]) -> SetupScript:
    """The script that runs `steps` in a sandbox holding the staged fixture, and the commits it makes.

    Args:
        steps: the scenario's resolved setup, in order.
        pending: the fixture's pending/ files, keyed by their path inside
            pending/ (app.py), as split_pending returns them.

    Returns:
        A SetupScript; its script is None when `steps` is empty.

    Raises:
        SetupError: an unknown step, parameters its model refuses, or
            apply_pending with no pending files.
    """
    body, commits = [], 0
    for index, call in enumerate(steps):
        step = STEPS.get(call.step)
        if step is None:
            raise SetupError(f"setup[{index}]: unknown setup step '{call.step}'; setup steps: {', '.join(STEPS)}")
        try:
            params = step.params.model_validate(call.params)
        except ValidationError as e:
            raise SetupError(f"setup[{index}]: {call.step}: {e}") from e
        body.append(step.script(params, pending))
        commits += step.commits
    if not body:
        return SetupScript(script=None, initial_commits=0)
    return SetupScript(script=f"set -e\n{ISOLATED_GIT_SHELL}cd {PROJECT_DIR}\n" + "".join(body), initial_commits=commits)


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
