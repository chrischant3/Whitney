"""Git without the user's git config, hooks or inherited GIT_* variables, for local runs.

Local runs share the user's machine. A global hook such as a secret scanner
would block the agent's bad commit and credit the model with it; a global
excludes file listing .env would make `git add -A` skip it; and the GIT_DIR
and GIT_INDEX_FILE that git exports to hooks would send fixture and agent
commits into the user's own repository.
"""

import shlex
from pathlib import Path

# A fixed identity (the one the Docker image sets) and none of the user's git
# config. GIT_CONFIG_COUNT replaces any command-scope config the caller
# exported, and turns off core.excludesFile, which git otherwise reads from
# $XDG_CONFIG_HOME/git/ignore even without a global config.
ISOLATED_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "core.excludesFile",
    "GIT_CONFIG_VALUE_0": "/dev/null",
    "GIT_AUTHOR_NAME": "Eval Agent",
    "GIT_AUTHOR_EMAIL": "agent@example.invalid",
    "GIT_COMMITTER_NAME": "Eval Agent",
    "GIT_COMMITTER_EMAIL": "agent@example.invalid",
}

# For shell scripts that run with the caller's environment, as Inspect's local
# sandbox runs setup and scorer commands: unset every inherited GIT_* variable,
# then export ISOLATED_GIT_ENV.
ISOLATED_GIT_SHELL = (
    "for name in $(env | sed -n 's/^\\(GIT_[A-Za-z0-9_]*\\)=.*/\\1/p'); do unset \"$name\"; done\n"
    + "".join(f"export {name}={shlex.quote(value)}\n" for name, value in ISOLATED_GIT_ENV.items())
)


def isolated_git_env(project_dir: Path) -> dict[str, str]:
    """ISOLATED_GIT_ENV for an agent in `project_dir`, whose git can't climb above it to find another repository."""
    return {**ISOLATED_GIT_ENV, "GIT_CEILING_DIRECTORIES": str(Path(project_dir).parent)}
