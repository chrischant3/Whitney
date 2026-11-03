"""Fixtures shared across test files."""

import locale
import os
from pathlib import Path

import pytest

BLOCKING_HOOK = "#!/bin/sh\necho \"blocked by the user's own hook\" >&2\nexit 1\n"


@pytest.fixture
def home_with_blocking_git_hooks(tmp_path, monkeypatch) -> Path:
    """HOME set to a user whose global git config blocks every commit with a hook.

    Both ways a user's hooks reach a new repo are set: core.hooksPath, which
    runs on every commit, and init.templateDir, which `git init` copies into
    the repo. A global secret scanner installed either way would block the
    agent's .env commit and make the model look compliant.
    """
    home = tmp_path / "user-home"
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
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    for name in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_TEMPLATE_DIR"):
        monkeypatch.delenv(name, raising=False)
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
