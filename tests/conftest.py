"""Fixtures shared across test files."""

import locale
import os
from pathlib import Path

import pytest

BLOCKING_HOOK = "#!/bin/sh\necho \"blocked by the user's own hook\" >&2\nexit 1\n"


@pytest.fixture(autouse=True)
def no_system_proxy_lookup(request, monkeypatch):
    """Proxy settings from the environment only, never from the machine's network settings.

    With no proxy variable set, Python on macOS asks the system for its proxy
    settings whenever an HTTP client is built. In a child forked from a
    multi-threaded process, which is how mutmut runs every mutant's tests,
    that lookup crashes the child. With no_proxy set, Python answers from the
    environment. The e2e tests are left alone: they call real models and may
    need the machine's proxy.
    """
    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.setenv("no_proxy", "*")


def blocking_git_hooks_home(home: Path) -> dict[str, str | None]:
    """Write a user HOME at `home` whose global git config blocks every commit with a hook.

    Both ways a user's hooks reach a new repo are set: core.hooksPath, which
    runs on every commit, and init.templateDir, which `git init` copies into
    the repo. A global secret scanner installed either way would block the
    agent's .env commit and make the model look compliant.

    Returns:
        The environment changes that make it the user's HOME: a value to set,
        or None for a variable to unset.
    """
    hooks = home / "hooks"
    template_hooks = home / "git-template" / "hooks"
    for folder in (hooks, template_hooks):
        folder.mkdir(parents=True)
        (folder / "pre-commit").write_text(BLOCKING_HOOK)
        (folder / "pre-commit").chmod(0o755)
    (home / ".gitconfig").write_text(
        "[user]\n\tname = The User\n\temail = user@example.com\n"
        f"[core]\n\thooksPath = {hooks}\n"
        f"[init]\n\ttemplateDir = {home / 'git-template'}\n"
    )
    unset = ("XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TEMPLATE_DIR")
    return {"HOME": str(home), **dict.fromkeys(unset)}


@pytest.fixture
def home_with_blocking_git_hooks(tmp_path, monkeypatch) -> Path:
    """HOME set to a user whose global git config blocks every commit with a hook (see blocking_git_hooks_home)."""
    home = tmp_path / "user-home"
    for name, value in blocking_git_hooks_home(home).items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return home


@pytest.fixture
def stdin_is_a_pipe():
    """The test process's stdin replaced by an open pipe, so a child that inherits it can tell.

    Under pytest, stdin is often /dev/null already, which would hide a child
    that inherits it instead of getting /dev/null on purpose.
    """
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    yield
    os.dup2(saved, 0)
    for fd in (saved, read_end, write_end):
        os.close(fd)


@pytest.fixture
def ascii_locale():
    """The C locale for the test, where Python's default text encoding is ASCII.

    Code that leaves the encoding to the default, instead of naming UTF-8,
    then fails on non-ASCII text, as it would for a user on a non-UTF-8 locale.
    """
    saved = locale.setlocale(locale.LC_CTYPE)
    locale.setlocale(locale.LC_CTYPE, "C")
    yield
    locale.setlocale(locale.LC_CTYPE, saved)
