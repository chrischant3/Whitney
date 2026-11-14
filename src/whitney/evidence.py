"""Collecting the evidence a scenario's checks declare, in the sandbox, and the secrets nothing stored may hold.

Around the agent, the generic task (whitney.tasks.scenario_eval) collects only
what its checks need (CheckSpec.needs):

- the run record the agent stored under RUN_STORE_KEY, already redacted;
- `snapshots`: the files under each watched fixture folder, taken after
  setup and again after the run, `.git/` left out (snapshot). Each file gets
  a SHA-256, a size and, for a UTF-8 file up to MAX_SNAPSHOT_FILE_BYTES, its
  text. A larger file is marked unreadable, and a folder holding more than
  MAX_SNAPSHOT_FILES files is marked incomplete; a check that needs it is
  then inconclusive (whitney.checks.snapshot_problem);
- `git`: the repository's history after the run (scoring.GIT_STATE_SCRIPT),
  searched for the check's findable tokens;
- `initial_commits`, from the setup.

Snapshots are held in memory for the checks and never written to the log.
Inspect logs every sandbox command with its output, so the commands that
read files the agent wrote run with the sandbox's event recording off.
Whatever a check stores goes through agent.redact with run_secrets.
"""

import base64
import contextlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from inspect_ai.util import OutputLimitExceededError
from inspect_ai.util._sandbox.events import SandboxEnvironmentProxy

from whitney.agent import ENV_REFERENCE, PROJECT_DIR, global_provider_settings, login_secrets, provider_secrets, proxy_secrets, redact, url_credentials
from whitney.checks import CheckSpec, Evidence, FileSnapshot, FolderSnapshot
from whitney.scoring import GIT_STATE_SCRIPT, GitState, parse_git_state

MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_FILES = 10_000
# A file larger than this isn't hashed, which would take minutes, but listed as unreadable.
MAX_HASHED_BYTES = 1024**3
# Seconds before a snapshot or git command is stopped, so an agent's hanging file can't stall the scorer.
COMMAND_TIMEOUT = 300
# Inspect refuses a command whose output passes 10 MiB; base64 makes 4 MiB about 5.4 MiB.
READ_BATCH_BYTES = 4 * 1024 * 1024

# Run as: python -I -c SNAPSHOT_SCRIPT list <max files> <max hashed bytes> <folder>...
#     or: python -I -c SNAPSHOT_SCRIPT read <max file bytes>, with a JSON list of paths on stdin.
# `list` prints, per folder, each entry's size and SHA-256 (a symlink's of its
# target's bytes, never followed), and whether the folder was listed in full:
# not if it held more than <max files>, part of it couldn't be listed, or it is
# a symlink or a file. A file over <max hashed bytes>, anything that isn't a
# regular file or symlink, and an entry whose name isn't UTF-8 are listed as
# unreadable; a name that isn't UTF-8 is shown with backslash escapes, so
# nothing printed holds bytes JSON or a score can't carry.
# `read` prints each path's bytes as base64, leaving out a path it can't read
# or that is over <max file bytes> (it may have grown since it was listed).
# -I keeps the sandbox's own files, which the agent may have written, off the
# module path.
SNAPSHOT_SCRIPT = r"""
import base64, hashlib, json, os, stat, sys


def shown(raw):
    return raw.decode("utf-8", "backslashreplace")


def entry(path, max_hashed):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        return {"size": len(target), "sha256": hashlib.sha256(target).hexdigest(), "symlink": shown(target)}
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_hashed:
        return {"size": info.st_size, "unreadable": True}
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return {"size": info.st_size, "sha256": digest.hexdigest()}


def name(relative, found):
    try:
        return relative.decode("utf-8"), found
    except UnicodeDecodeError:
        return shown(relative), {"size": found["size"], "unreadable": True}


def walk(folder, max_files, max_hashed):
    top = os.fsencode(folder)
    if not os.path.lexists(top):
        return {"files": {}, "complete": True}
    if os.path.islink(top) or not os.path.isdir(top):
        return {"files": {}, "complete": False}
    files, errors = {}, []
    for root, dirs, names in os.walk(top, onerror=errors.append):
        links = [d for d in dirs if os.path.islink(os.path.join(root, d))]
        dirs[:] = sorted(d for d in dirs if d != b".git" and d not in links)
        for entry_name in sorted(names + links):
            if len(files) == max_files:
                return {"files": files, "complete": False}
            path = os.path.join(root, entry_name)
            try:
                found = entry(path, max_hashed)
            except (OSError, ValueError):
                found = {"size": 0, "unreadable": True}
            key, found = name(os.path.relpath(path, top).replace(os.sep.encode(), b"/"), found)
            files[key] = found
    return {"files": files, "complete": not errors}


def read(limit, paths):
    out = {}
    for path in paths:
        try:
            with open(path, "rb") as f:
                content = f.read(limit + 1)
        except OSError:
            continue  # left out, so the reader marks it unreadable
        if len(content) <= limit:
            out[path] = base64.b64encode(content).decode("ascii")
    return out


if __name__ == "__main__":
    if sys.argv[1] == "list":
        max_files, max_hashed = int(sys.argv[2]), int(sys.argv[3])
        print(json.dumps({folder: walk(folder, max_files, max_hashed) for folder in sys.argv[4:]}))
    elif sys.argv[1] == "read":
        print(json.dumps(read(int(sys.argv[2]), json.load(sys.stdin))))
    else:
        sys.exit("usage: list <max files> <max hashed bytes> <folder>... | read <max file bytes>")
"""


@dataclass(frozen=True)
class EvidencePlan:
    """What to collect for one scenario, from the checks it names.

    Attributes:
        needs: the evidence any of its checks needs (whitney.checks.NEEDS).
        folders: the fixture folders to snapshot, in the order first named;
            only a check that needs "snapshots" names any.
        tokens: the strings to search the git history for: every findable
            parameter of a check that needs "git", in the order first named.
    """

    needs: frozenset[str]
    folders: tuple[str, ...]
    tokens: tuple[str, ...]


def plan(checks: Iterable[tuple[CheckSpec, Mapping[str, Any]]]) -> EvidencePlan:
    """The evidence a scenario's checks need, given each check and its parameters.

    Raises:
        pydantic.ValidationError: a check's parameters aren't valid.
    """
    needs: set[str] = set()
    folders: dict[str, None] = {}
    tokens: dict[str, None] = {}
    for spec, params in checks:
        parsed = spec.parse_params(params)
        needs.update(spec.needs)
        if "snapshots" in spec.needs:
            folders.update(dict.fromkeys(spec.folders(parsed)))
        if "git" in spec.needs:
            for name in spec.findable:
                tokens.update(dict.fromkeys(_strings(getattr(parsed, name))))
    return EvidencePlan(frozenset(needs), tuple(folders), tuple(tokens))


def _strings(value: Any) -> list[str]:
    """A parameter's value as the strings it holds: itself if a string, else its items."""
    return [value] if isinstance(value, str) else list(value)


async def collect(
    sbox, evidence_plan: EvidencePlan, python: str, run: Mapping[str, Any], *, initial_commits: int | None,
    before: Mapping[str, FolderSnapshot], secrets: Iterable[str] = (),
) -> Evidence:
    """The Evidence for one run: the run record plus what `evidence_plan` asks for, collected in `sbox` now.

    Args:
        sbox: the sample's sandbox, after the run.
        evidence_plan: from plan.
        python: the sandbox's Python, for the snapshot script.
        run: the run record the agent stored under RUN_STORE_KEY.
        initial_commits: the commits the setup made.
        before: the snapshots taken after setup (snapshot), by folder.
        secrets: the run's, to redact from a snapshot error (run_secrets).
    """
    git = await git_state(sbox, evidence_plan.tokens) if "git" in evidence_plan.needs else None
    after = await snapshot(sbox, evidence_plan.folders, python, secrets=secrets)
    return Evidence.from_run(
        run,
        initial_commits=initial_commits,
        git=git,
        before={folder: taken.texts() for folder, taken in before.items()},
        after={folder: taken.texts() for folder, taken in after.items()},
        snapshots_before=dict(before),
        snapshots_after=after,
    )


async def git_state(sbox, tokens: Iterable[str]) -> GitState:
    """The project's git history in `sbox` (GIT_STATE_SCRIPT), searched for `tokens`, which it records as searched."""
    tokens = list(tokens)
    with _quietly(sbox):
        result = await sbox.exec(["sh", "-c", GIT_STATE_SCRIPT, "sh", PROJECT_DIR, *tokens], timeout=COMMAND_TIMEOUT)
    return replace(parse_git_state(result.stdout), searched=set(tokens))


async def snapshot(
    sbox, folders: Iterable[str], python: str, *, max_file_bytes: int = MAX_SNAPSHOT_FILE_BYTES,
    max_files: int = MAX_SNAPSHOT_FILES, max_hashed_bytes: int = MAX_HASHED_BYTES,
    read_batch_bytes: int = READ_BATCH_BYTES, timeout: int = COMMAND_TIMEOUT, secrets: Iterable[str] = (),
) -> dict[str, FolderSnapshot]:
    """Snapshots of `folders` (relative to the sandbox root) in `sbox`; a missing folder is empty.

    Every file is listed with its hash and size; then the text of each regular
    file up to `max_file_bytes` is read, in batches of at most
    `read_batch_bytes` (paths included), so no one command's output passes
    Inspect's limit. A listing too large for that limit leaves every folder
    empty and incomplete.

    Args:
        python: the sandbox's Python: the running interpreter in local
            execution, python3 in the Docker image.
        timeout: seconds each command may take.
        secrets: values to redact from an error's stderr.

    Raises:
        RuntimeError: a snapshot command failed, as when `python` isn't there.
        TimeoutError: a command took longer than `timeout`.
    """
    folders = list(folders)
    if not folders:
        return {}
    script = [python, "-I", "-c", SNAPSHOT_SCRIPT]
    with _quietly(sbox):
        try:
            listing = await _json_output(
                sbox, [*script, "list", str(max_files), str(max_hashed_bytes), *folders], timeout=timeout, secrets=secrets
            )
        except OutputLimitExceededError:
            return {folder: FolderSnapshot({}, complete=False) for folder in folders}
        wanted = [
            (f"{folder}/{path}", found["size"])
            for folder, listed in listing.items()
            for path, found in listed["files"].items()
            if _worth_reading(path, found, max_file_bytes)
        ]
        contents: dict[str, str] = {}
        for batch in _batches(wanted, read_batch_bytes):
            command = [*script, "read", str(max_file_bytes)]
            contents.update(await _json_output(sbox, command, input=json.dumps(batch), timeout=timeout, secrets=secrets))
    return parse_snapshot(listing, contents, max_file_bytes=max_file_bytes)


def parse_snapshot(listing: Mapping[str, Any], contents: Mapping[str, str], *, max_file_bytes: int) -> dict[str, FolderSnapshot]:
    """FolderSnapshots from SNAPSHOT_SCRIPT's `list` output and the base64 `contents` read for it, by sandbox path.

    A file that should have been read but wasn't (it went, or grew, between
    the listing and the read) is unreadable. So is a name holding lone
    surrogates, which no score could carry; it is kept with them escaped.
    """
    snapshots = {}
    for folder, listed in listing.items():
        files = {}
        for path, found in listed["files"].items():
            content = contents.get(f"{folder}/{path}")
            readable = _worth_reading(path, found, max_file_bytes) and content is not None
            files[_safe(path)] = FileSnapshot(
                sha256=found.get("sha256", ""),
                size=found["size"],
                text=_text(content) if readable else None,
                unreadable="unreadable" in found or (_is_regular(found) and not readable),
                symlink=found.get("symlink"),
            )
        snapshots[folder] = FolderSnapshot(files, complete=listed["complete"])
    return snapshots


def _safe(path: str) -> str:
    """`path` with any lone surrogate written as a backslash escape."""
    return path.encode("utf-8", "backslashreplace").decode("utf-8")


def _is_regular(found: Mapping[str, Any]) -> bool:
    return "symlink" not in found and not found.get("unreadable")


def _worth_reading(path: str, found: Mapping[str, Any], max_file_bytes: int) -> bool:
    return _is_regular(found) and found["size"] <= max_file_bytes and _safe(path) == path


def _text(content: str) -> str | None:
    """The base64 `content` as UTF-8 text, or None if it isn't UTF-8."""
    try:
        return base64.b64decode(content).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _batches(sized: list[tuple[str, int]], limit: int) -> list[list[str]]:
    """The paths in order, grouped so each group's sizes and path lengths add up to at most `limit` (or one file, if it alone is larger)."""
    batches: list[list[str]] = []
    totals: list[int] = []
    for path, size in sized:
        weight = size + len(path)
        if batches and totals[-1] + weight <= limit:
            batches[-1].append(path)
            totals[-1] += weight
        else:
            batches.append([path])
            totals.append(weight)
    return batches


async def _json_output(sbox, command: list[str], *, timeout: int, secrets: Iterable[str], input: str | None = None) -> Any:
    result = await sbox.exec(command, input=input, timeout=timeout)
    if result.returncode != 0:
        # Its stderr can quote a file the agent wrote, and the error reaches the log.
        raise RuntimeError(f"the snapshot of the sandbox's files failed: {redact(result.stderr, list(secrets)).strip()[-300:]}")
    return json.loads(result.stdout)


def _quietly(sbox) -> contextlib.AbstractContextManager:
    """A block in which `sbox` records no events, if it is Inspect's event-recording proxy.

    The proxy's `no_events()` is what Inspect's own sandbox service uses
    (inspect_ai.util._sandbox.events.without_sandbox_events, which isn't
    public). Any other sandbox, such as a test's, records nothing anyway.

    Raises:
        RuntimeError: a proxy without no_events(), as an Inspect older or newer
            than this code knows would have; its log would get the files' text.
    """
    if not isinstance(sbox, SandboxEnvironmentProxy):
        return contextlib.nullcontext()
    if not callable(sbox.no_events):
        raise RuntimeError(
            "this Inspect's sandbox can't turn off its event log, so a snapshot would write the files' text "
            "into the eval log; use the Inspect version in requirements.lock"
        )
    return sbox.no_events()


def run_secrets(
    target: str, execution: str, api_key_env: str | None, environ: Mapping[str, str] = os.environ, base_url: str | None = None
) -> list[str]:
    """The secret values a run of `target` is given, which nothing Whitney stores may hold (see agent.redact).

    The same values the agents redact from their own run records: for
    claude-code, its API key (api_key_env, default ANTHROPIC_API_KEY; in
    Docker mode that is Inspect's key on the host, which the container never
    has, redacted all the same); for a
    local kilo run, the variables its config overlay names as {env:NAME}
    (those in the global Kilo config's provider settings, and api_key_env),
    the values in the copy of the user's Kilo login, the literal secrets in
    the provider settings it copies (agent.provider_secrets) and the proxy
    credentials it passes on (agent.proxy_secrets), and the user and
    password in base_url, the `--base-url` its overlay gives the provider
    (agent.url_credentials). A Docker kilo run is
    given none: Inspect's bridge answers it, its global config is written
    fresh and its environment holds no host variable.
    """
    if target == "claude-code":
        return _set_values([api_key_env or "ANTHROPIC_API_KEY"], environ)
    if execution != "local":
        return []
    providers = global_provider_settings(environ)
    referenced = ENV_REFERENCE.findall(json.dumps(providers))
    copied = provider_secrets({"provider": providers}) + proxy_secrets(environ) + (url_credentials(base_url) if base_url else [])
    return _set_values([*referenced, *filter(None, [api_key_env])], environ) + login_secrets(environ) + copied


def _set_values(names: Iterable[str], environ: Mapping[str, str]) -> list[str]:
    """The values of the variables in `names` that are set, each once, in the order named."""
    return [environ[name] for name in dict.fromkeys(names) if environ.get(name)]
