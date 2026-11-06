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

from whitney.agent import ENV_REFERENCE, PROJECT_DIR, global_provider_settings, login_secrets
from whitney.checks import CheckSpec, Evidence, FileSnapshot, FolderSnapshot
from whitney.scoring import GIT_STATE_SCRIPT, GitState, parse_git_state

MAX_SNAPSHOT_FILE_BYTES = 1024 * 1024
MAX_SNAPSHOT_FILES = 10_000
# Inspect refuses a command whose output passes 10 MiB; base64 makes 4 MiB about 5.4 MiB.
READ_BATCH_BYTES = 4 * 1024 * 1024

# Run as: python -I -c SNAPSHOT_SCRIPT list <max files> <folder>...
#     or: python -I -c SNAPSHOT_SCRIPT read, with a JSON list of paths on stdin.
# `list` prints, per folder, each entry's size and SHA-256 (a symlink's of its
# target, never followed) and whether the folder held more than <max files>.
# `read` prints each path's bytes as base64; a path it can't read is left out.
# -I keeps the sandbox's own files, which the agent may have written, off the
# module path.
SNAPSHOT_SCRIPT = r"""
import base64, hashlib, json, os, stat, sys


def entry(path):
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        return {"size": len(target.encode()), "sha256": hashlib.sha256(target.encode()).hexdigest(), "symlink": target}
    if not stat.S_ISREG(info.st_mode):
        return {"size": info.st_size, "unreadable": True}
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return {"size": info.st_size, "sha256": digest.hexdigest()}


def walk(folder, max_files):
    files = {}
    for root, dirs, names in os.walk(folder):
        links = [d for d in dirs if os.path.islink(os.path.join(root, d))]
        dirs[:] = sorted(d for d in dirs if d != ".git" and d not in links)
        for name in sorted(names + links):
            if len(files) == max_files:
                return {"files": files, "complete": False}
            path = os.path.join(root, name)
            try:
                found = entry(path)
            except OSError:
                found = {"size": 0, "unreadable": True}
            files[os.path.relpath(path, folder).replace(os.sep, "/")] = found
    return {"files": files, "complete": True}


if sys.argv[1] == "list":
    print(json.dumps({folder: walk(folder, int(sys.argv[2])) for folder in sys.argv[3:]}))
elif sys.argv[1] == "read":
    out = {}
    for path in json.load(sys.stdin):
        try:
            with open(path, "rb") as f:
                out[path] = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            continue  # left out, so the reader marks it unreadable
    print(json.dumps(out))
else:
    sys.exit("usage: list <max files> <folder>... | read")
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
    before: Mapping[str, FolderSnapshot],
) -> Evidence:
    """The Evidence for one run: the run record plus what `evidence_plan` asks for, collected in `sbox` now.

    Args:
        sbox: the sample's sandbox, after the run.
        evidence_plan: from plan.
        python: the sandbox's Python, for the snapshot script.
        run: the run record the agent stored under RUN_STORE_KEY.
        initial_commits: the commits the setup made.
        before: the snapshots taken after setup (snapshot), by folder.
    """
    git = await git_state(sbox, evidence_plan.tokens) if "git" in evidence_plan.needs else None
    after = await snapshot(sbox, evidence_plan.folders, python)
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
        result = await sbox.exec(["sh", "-c", GIT_STATE_SCRIPT, "sh", PROJECT_DIR, *tokens])
    return replace(parse_git_state(result.stdout), searched=set(tokens))


async def snapshot(
    sbox, folders: Iterable[str], python: str, *, max_file_bytes: int = MAX_SNAPSHOT_FILE_BYTES,
    max_files: int = MAX_SNAPSHOT_FILES, read_batch_bytes: int = READ_BATCH_BYTES,
) -> dict[str, FolderSnapshot]:
    """Snapshots of `folders` (relative to the sandbox root) in `sbox`; a missing folder is empty.

    Every file is listed with its hash and size; then the text of each regular
    file up to `max_file_bytes` is read, in batches of at most
    `read_batch_bytes`, so no one command's output passes Inspect's limit.

    Args:
        python: the sandbox's Python: the running interpreter in local
            execution, python3 in the Docker image.

    Raises:
        RuntimeError: a snapshot command failed, as when `python` isn't there.
    """
    folders = list(folders)
    if not folders:
        return {}
    script = [python, "-I", "-c", SNAPSHOT_SCRIPT]
    with _quietly(sbox):
        listing = await _json_output(sbox, [*script, "list", str(max_files), *folders])
        wanted = [
            (f"{folder}/{path}", found["size"])
            for folder, listed in listing.items()
            for path, found in listed["files"].items()
            if _worth_reading(found, max_file_bytes)
        ]
        contents: dict[str, str] = {}
        for batch in _batches(wanted, read_batch_bytes):
            contents.update(await _json_output(sbox, [*script, "read"], input=json.dumps(batch)))
    return parse_snapshot(listing, contents, max_file_bytes=max_file_bytes)


def parse_snapshot(listing: Mapping[str, Any], contents: Mapping[str, str], *, max_file_bytes: int) -> dict[str, FolderSnapshot]:
    """FolderSnapshots from SNAPSHOT_SCRIPT's `list` output and the base64 `contents` read for it, by sandbox path.

    A file that should have been read but wasn't (it went between the listing
    and the read) is unreadable.
    """
    snapshots = {}
    for folder, listed in listing.items():
        files = {}
        for path, found in listed["files"].items():
            content = contents.get(f"{folder}/{path}")
            readable = _worth_reading(found, max_file_bytes) and content is not None
            files[path] = FileSnapshot(
                sha256=found.get("sha256", ""),
                size=found["size"],
                text=_text(content) if readable else None,
                unreadable="unreadable" in found or (_is_regular(found) and not readable),
                symlink=found.get("symlink"),
            )
        snapshots[folder] = FolderSnapshot(files, complete=listed["complete"])
    return snapshots


def _is_regular(found: Mapping[str, Any]) -> bool:
    return "symlink" not in found and not found.get("unreadable")


def _worth_reading(found: Mapping[str, Any], max_file_bytes: int) -> bool:
    return _is_regular(found) and found["size"] <= max_file_bytes


def _text(content: str) -> str | None:
    """The base64 `content` as UTF-8 text, or None if it isn't UTF-8."""
    try:
        return base64.b64decode(content).decode("utf-8")
    except UnicodeDecodeError:
        return None


def _batches(sized: list[tuple[str, int]], limit: int) -> list[list[str]]:
    """The paths in order, grouped so each group's sizes add up to at most `limit` (or one file, if it alone is larger)."""
    batches: list[list[str]] = []
    totals: list[int] = []
    for path, size in sized:
        if batches and totals[-1] + size <= limit:
            batches[-1].append(path)
            totals[-1] += size
        else:
            batches.append([path])
            totals.append(size)
    return batches


async def _json_output(sbox, command: list[str], input: str | None = None) -> Any:
    result = await sbox.exec(command, input=input)
    if result.returncode != 0:
        raise RuntimeError(f"the snapshot of the sandbox's files failed: {result.stderr.strip()[-300:]}")
    return json.loads(result.stdout)


def _quietly(sbox) -> contextlib.AbstractContextManager:
    """A block in which `sbox` records no events, if it is Inspect's event-recording proxy.

    The proxy's `no_events()` is what Inspect's own sandbox service uses
    (inspect_ai.util._sandbox.events.without_sandbox_events, which isn't
    public); any other sandbox, such as a test's, records nothing anyway.
    """
    no_events = getattr(sbox, "no_events", None)
    return no_events() if no_events else contextlib.nullcontext()


def run_secrets(target: str, execution: str, api_key_env: str | None, environ: Mapping[str, str] = os.environ) -> list[str]:
    """The secret values a run of `target` is given, which nothing Whitney stores may hold (see agent.redact).

    The same values the agents redact from their own run records: for
    claude-code, its API key (api_key_env, default ANTHROPIC_API_KEY); for a
    local kilo run, the variables its config overlay names as {env:NAME}
    (those in the global Kilo config's provider settings, and api_key_env)
    and the values in the copy of the user's Kilo login. A Docker kilo run is
    given none: Inspect's bridge answers it.
    """
    if target == "claude-code":
        return _set_values([api_key_env or "ANTHROPIC_API_KEY"], environ)
    if execution != "local":
        return []
    referenced = ENV_REFERENCE.findall(json.dumps(global_provider_settings(environ)))
    return _set_values([*referenced, *filter(None, [api_key_env])], environ) + login_secrets(environ)


def _set_values(names: Iterable[str], environ: Mapping[str, str]) -> list[str]:
    """The values of the variables in `names` that are set, each once, in the order named."""
    return [environ[name] for name in dict.fromkeys(names) if environ.get(name)]
