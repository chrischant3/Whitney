"""whitney watch: tell the person in a live Claude Code session when the agent breaks a rule, naming the rule.

Whitney's other commands judge a controlled run after it ends. This judges a
person's own session while it happens, with the same checks (whitney.checks),
and only alerts: it never blocks a tool call or tells the model anything
(tasks/plan-later.md, Task 66, agreed 2026-11-15).

How it fits together:

- `whitney watch install` writes command hooks into the project's
  `.claude/settings.local.json` (SessionStart, PreToolUse, PostToolUse,
  PostToolUseFailure and Stop), each
  running whitney/watch_client.py, and a rules file, `.whitney/watch.json`,
  if there isn't one.
- `whitney watch` runs the watcher: one long-lived process that has already
  imported the checks, listening on a Unix socket in a folder only this user
  can open. The hook client hands it each event and prints its reply.
- At PreToolUse, with no other tool call running, commits that appeared since
  the last call are taken as the person's own and never judged. At
  PostToolUse, or PostToolUseFailure for a call that failed, commits that
  appeared during the call are judged against every
  rule, and an alert comes back as the hook's systemMessage, which Claude Code
  shows the person and not the model (code.claude.com/docs/en/hooks, read
  2026-11-15).

What a rule can be judged by today: no_secret_committed, on the commits the
agent made, searched for the values in the project's own .env files. An alert
quotes the rule and its source file, says what the check found and in which
tool call, and never holds a secret's value.

Known limits: a commit no ref or reflog reaches (git commit-tree with no
update-ref) isn't seen; a commit the person makes while a tool call is running
is judged as the agent's; Unix only (the socket); Claude Code only (Kilo is
the next step, through a plugin).
"""

import json
import os
import shlex
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from pydantic import ValidationError

from whitney import watch_client
from whitney.checks import Evidence, Verdict, registry
from whitney.scoring import GitState, _is_env_file

RULES_FILE = ".whitney/watch.json"
SETTINGS_FILE = ".claude/settings.local.json"
# The checks whitney watch can feed from a live session.
LIVE_CHECKS = ("no_secret_committed",)
# The template's core rule (template/claude-code/AGENTS.md), which `install` watches by default.
DEFAULT_RULE = "Never commit .env files or secrets"
DEFAULT_SOURCES = ("AGENTS.md", "CLAUDE.md")
# Seconds Claude Code gives each hook. The client gives up on the watcher sooner (watch_client.TIMEOUT).
HOOK_TIMEOUT = 10
# PostToolUseFailure runs instead of PostToolUse for a call that failed, which can still have committed;
# Stop ends the turn, so a call that never finished, such as a denied one, stops counting as running.
HOOK_EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop")
# Events whose hook entry has no matcher: they aren't about a tool.
NO_MATCHER = ("SessionStart", "Stop")
FINISHED = ("PostToolUse", "PostToolUseFailure")
# A call still running after this many seconds is taken to have ended without a PostToolUse (a denied call).
IN_FLIGHT_SECONDS = 600
# A value shorter than this in a .env file (a port, a flag) isn't searched for: it would match everywhere.
MIN_SECRET_LENGTH = 8
MAX_ENV_FILE_BYTES = 1024 * 1024
# A committed file larger than this isn't searched for secrets, and the verdict says so.
MAX_BLOB_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
# git without locks it can do without (the agent's own git may be running), replace refs or prompts.
GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0"}
# The most characters of a tool call's command an alert shows.
MAX_SHOWN = 200
MAX_REQUEST = 1024 * 1024
FAILED = "whitney watch: a rule wasn't followed"
UNSURE = "whitney watch: couldn't tell whether a rule was followed"


class WatchError(Exception):
    """A problem the person has to fix, said in words they can act on."""


@dataclass(frozen=True)
class Rule:
    """One rule the watcher judges.

    Attributes:
        source: the instruction file the rule is in, relative to the project.
        rule: the rule's words, as the alert quotes them.
        check: the check that judges it, one of LIVE_CHECKS.
        params: the check's parameters, other than the tokens, which the
            watcher takes from the project's .env files.
    """

    source: str
    rule: str
    check: str
    params: Mapping[str, Any] = field(default_factory=dict)


def load_rules(project: Path) -> list[Rule]:
    """The rules in the project's RULES_FILE.

    Raises:
        WatchError: the file is missing or isn't a valid rules file.
    """
    path = Path(project) / RULES_FILE
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise WatchError(f"no rules file at {path}; run: whitney watch install") from None
    except ValueError:
        raise WatchError(f"{RULES_FILE} isn't valid JSON") from None
    if not isinstance(content, dict):
        raise WatchError(f"{RULES_FILE} must be a JSON object")
    _no_unknown(content, {"version", "rules"}, f"{RULES_FILE}")
    if content.get("version") != 1:
        raise WatchError(f"{RULES_FILE}: version must be 1")
    rules = content.get("rules")
    if not isinstance(rules, list):
        raise WatchError(f"{RULES_FILE} needs a list of rules")
    if not rules:
        raise WatchError(f"{RULES_FILE} holds no rules")
    return [_rule(entry, number) for number, entry in enumerate(rules, start=1)]


def _rule(entry: object, number: int) -> Rule:
    where = f"{RULES_FILE}: rule {number}"
    if not isinstance(entry, dict):
        raise WatchError(f"{where} must be a JSON object")
    _no_unknown(entry, {"source", "rule", "check", "params"}, where)
    for key in ("source", "rule", "check"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise WatchError(f"{where} needs {key}")
    check = entry["check"]
    if check not in LIVE_CHECKS:
        raise WatchError(f"{RULES_FILE}: rule {number}'s check {check} can't be judged live; whitney watch can judge: {', '.join(LIVE_CHECKS)}")
    params = entry.get("params", {})
    if not isinstance(params, dict):
        raise WatchError(f"{RULES_FILE}: rule {number}'s params must be a JSON object")
    if "tokens" in params:
        raise WatchError(f"{RULES_FILE}: rule {number}'s params can't name tokens; the watcher takes them from the project's .env files")
    try:
        registry()[check].parse_params({**params, "tokens": ["checked"]})
    except ValidationError as error:
        raise WatchError(f"{RULES_FILE}: rule {number}'s params aren't valid for {check}: {error.errors()[0]['msg']}") from None
    return Rule(source=entry["source"], rule=entry["rule"], check=check, params=params)


def _no_unknown(entry: dict, known: set[str], where: str) -> None:
    unknown = sorted(set(entry) - known)
    if unknown:
        raise WatchError(f"{where} has unknown field(s): {', '.join(unknown)}")


def rule_missing(project: Path, source: str, words: str) -> str | None:
    """Why a rule's `words` aren't in its `source` file any more, or None if they are, whatever the spacing."""
    try:
        text = (Path(project) / source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f'{source}, the source of "{words}", can\'t be read'
    if " ".join(words.split()) in " ".join(text.split()):
        return None
    return f'"{words}" is no longer in {source}'


def env_secrets(project: Path) -> dict[str, str]:
    """The values in the project's .env-style files, by "<file> <name>", for the commits to be searched for.

    Templates such as .env.example are left out, as no_secret_committed leaves
    them out, and so are values shorter than MIN_SECRET_LENGTH, files over
    MAX_ENV_FILE_BYTES and the folders in SKIP_DIRS.
    """
    project = Path(project)
    secrets: dict[str, str] = {}
    for folder, dirs, files in os.walk(project):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(folder) / name
            if not _is_env_file(name):
                continue
            try:
                if path.stat().st_size > MAX_ENV_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            label = path.relative_to(project).as_posix()
            for key, value in _env_values(text):
                secrets[f"{label} {key}"] = value
    return secrets


def _env_values(text: str) -> list[tuple[str, str]]:
    values = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :]
        key, equals, value = line.partition("=")
        if not equals or line.startswith("#"):
            continue
        value = value.strip()
        quote = value[:1]
        if quote in ("'", '"') and value.endswith(quote):
            value = value[1:-1]
        else:
            value = value.split(" #")[0].strip()
        if len(value) >= MIN_SECRET_LENGTH:
            values.append((key.strip(), value))
    return values


class Repo:
    """Read-only git for the watched project, without inherited GIT_* variables or optional locks.

    A hook's environment can carry GIT_DIR or GIT_INDEX_FILE, which would send
    git into another repository, and a status-like command can take the index
    lock while the agent's own git is running.
    """

    def __init__(self, project: Path):
        self.project = Path(project)
        self.env = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        self.env.update(GIT_ENV)
        try:
            self.git_dir = Path(self.git("rev-parse", "--absolute-git-dir").decode().strip())
        except WatchError:
            raise WatchError(f"{project} isn't in a git repository, so commits can't be watched") from None

    def git(self, *args: str, stdin: bytes | None = None) -> bytes:
        """git's stdout.

        Raises:
            WatchError: git failed.
        """
        done = subprocess.run(["git", "-C", str(self.project), *args], input=stdin, capture_output=True, env=self.env)
        if done.returncode != 0:
            raise WatchError(f"git {args[0]} failed: {done.stderr.decode(errors='replace').strip()}")
        return done.stdout

    def fingerprint(self) -> tuple:
        """Something that changes whenever a commit is made: the refs, the HEAD file, and HEAD's reflog's size and time.

        The HEAD file names the commit on a detached HEAD, which no ref does;
        the reflog changes on a commit even where the refs end as they began.
        """
        refs = self.git("for-each-ref", "--format=%(objectname) %(refname)")
        head = (self.git_dir / "HEAD").read_bytes()
        log = self.git_dir / "logs" / "HEAD"
        reflog = (log.stat().st_size, log.stat().st_mtime_ns) if log.exists() else ()
        return refs, head, reflog

    def commits(self) -> set[str]:
        """Every commit a ref, HEAD or a reflog reaches."""
        return set(self.git("rev-list", "--all", "--reflog").decode().split())

    def introduced(self, commit: str) -> list[tuple[str, str]]:
        """(path, blob) for each file `commit` added or changed, against each of its parents."""
        # Plumbing: diff-tree never detects renames, whatever diff.renames says, so a file renamed to .env is added.
        raw = self.git("diff-tree", "-r", "-z", "--no-commit-id", "--root", "-m", "--diff-filter=AMT", commit)
        parts = raw.decode(errors="surrogateescape").split("\0")
        entries = [(path, meta.split()) for meta, path in zip(parts[0::2], parts[1::2])]
        # A submodule (mode 160000) is a commit, not a file whose content can be searched.
        return list(dict.fromkeys((path, fields[3]) for path, fields in entries if fields[1:2] != ["160000"]))

    def blobs(self, shas: Iterable[str]) -> dict[str, bytes | None]:
        """Each blob's content, or None for one over MAX_BLOB_BYTES or missing from the repository (a partial clone)."""
        shas = list(dict.fromkeys(shas))
        sizes = self.git("cat-file", "--batch-check=%(objectname) %(objectsize)", stdin="".join(f"{s}\n" for s in shas).encode())
        result: dict[str, bytes | None] = {}
        small = []
        for line in sizes.decode().splitlines():
            sha, size = line.split()
            result[sha] = None
            if size.isdigit() and int(size) <= MAX_BLOB_BYTES:
                small.append(sha)
        out = self.git("cat-file", "--batch", stdin="".join(f"{s}\n" for s in small).encode())
        while out:
            header, _, out = out.partition(b"\n")
            sha, _, size = header.decode().split()
            result[sha], out = out[: int(size)], out[int(size) + 1 :]
        return result


def git_state(repo: Repo, commits: list[str], secrets: Mapping[str, str]) -> GitState:
    """The git state no_secret_committed judges, for `commits` alone: what they added, and where a secret is.

    A hit names the commit, the file and which .env value it holds, never the value.
    """
    files: set[str] = set()
    places: dict[str, list[str]] = {}
    for commit in commits:
        for path, blob in repo.introduced(commit):
            files.add(path)
            places.setdefault(blob, []).append(f"{commit[:12]}:{path}")
    hits, problems = [], []
    if secrets:
        contents = repo.blobs(list(places))
        for blob, where in places.items():
            data = contents.get(blob)
            for place in where:
                if data is None:
                    problems.append(f"{place} is over {MAX_BLOB_BYTES} bytes or not in the local repository, so it wasn't searched for secrets")
                    continue
                hits += [f"{place} ({label})" for label, value in sorted(secrets.items()) if value.encode() in data]
    return GitState(commits=len(commits), files=files, hits=hits, problems=problems, searched=set(secrets.values()))


class Watcher:
    """The rules, the repository, and what has been judged so far. handle() is safe to call from many threads."""

    def __init__(self, project: Path, rules: list[Rule] | None = None):
        self.project = Path(project).resolve()
        self.rules = load_rules(self.project) if rules is None else rules
        self.repo = Repo(self.project)
        self.lock = threading.Lock()
        self.in_flight: dict[str, float] = {}
        self.known = self.repo.commits()
        self.seen = self.repo.fingerprint()
        self.alerts: list[dict] = []
        self.on_alert: Callable[[str], None] = _ignore

    def handle(self, hook: Mapping[str, Any]) -> dict:
        """The reply for one hook event: {"systemMessage": ...} to tell the person something, else {}."""
        event = hook.get("hook_event_name")
        with self.lock:
            try:
                if event == "SessionStart":
                    return {"systemMessage": self.watching()}
                if event == "Stop":
                    self.in_flight.clear()
                if event == "PreToolUse":
                    now = time.monotonic()
                    self.in_flight = {call: at for call, at in self.in_flight.items() if now - at <= IN_FLIGHT_SECONDS}
                    if not self.in_flight:
                        self.known |= self._new_commits()
                    self.in_flight[str(hook.get("tool_use_id"))] = now
                if event in FINISHED:
                    self.in_flight.pop(str(hook.get("tool_use_id")), None)
                    return self._judge(hook)
            except WatchError as error:
                return {"systemMessage": f"whitney watch couldn't judge this tool call: {error}"}
            except Exception as error:  # any other failure is said, never swallowed: a silent watcher looks like a clean one
                return {"systemMessage": f"whitney watch couldn't judge this tool call: {type(error).__name__}: {error}"}
        return {}

    def watching(self) -> str:
        """What SessionStart tells the person: the rules watched, and any no longer in their source."""
        count = len(self.rules)
        listed = "; ".join(f'"{rule.rule}" ({rule.source})' for rule in self.rules)
        lines = [f"whitney watch is watching {count} rule{'' if count == 1 else 's'}: {listed}"]
        lines += [f"Warning: {missing}" for rule in self.rules if (missing := rule_missing(self.project, rule.source, rule.rule))]
        return "\n".join(lines)

    def _new_commits(self) -> set[str]:
        seen = self.repo.fingerprint()
        if seen == self.seen:
            return set()
        new = self.repo.commits() - self.known
        self.seen = seen
        return new

    def _judge(self, hook: Mapping[str, Any]) -> dict:
        new = sorted(self._new_commits())
        if not new:
            return {}
        self.known |= set(new)
        secrets = env_secrets(self.project)
        tokens = sorted(set(secrets.values()))
        evidence = Evidence(exit_code=0, events=_tool_event(hook), initial_commits=0, git=git_state(self.repo, new, secrets))
        blocks = []
        for rule in self.rules:
            params = {"env_files": True, **rule.params, "tokens": tokens}
            if not tokens and not params["env_files"]:
                continue
            verdict = registry()[rule.check].run(evidence, params)
            if verdict.outcome == "pass":
                continue
            message = _redacted(alert_text(rule, verdict, new, hook), secrets.values())
            self.alerts.append({"time": time.time(), "outcome": verdict.outcome, "rule": rule.rule, "source": rule.source, "message": message})
            self.on_alert(message)
            blocks.append(message)
        return {"systemMessage": "\n\n".join(blocks)} if blocks else {}


def _ignore(message: str) -> None:
    """The watcher's alert callback until start_server sets one."""


def _tool_event(hook: Mapping[str, Any]) -> str:
    """The tool call's input as one event line, where history_erasure reads tool inputs (anything under "input").

    It is the only part of the stream no_secret_committed reads.
    """
    return json.dumps({"input": hook.get("tool_input")})


def alert_text(rule: Rule, verdict: Verdict, commits: list[str], hook: Mapping[str, Any]) -> str:
    """The alert for one rule: the rule and its source, what the check found, the commits and the tool call."""
    heading = FAILED if verdict.outcome == "fail" else UNSURE
    return "\n".join(
        [
            heading,
            f'Rule: "{rule.rule}" ({rule.source})',
            f"What happened: {verdict.explanation}",
            f"Commit(s): {', '.join(commit[:12] for commit in commits)}",
            f"In: {_tool_shown(hook)}",
        ]
    )


def _tool_shown(hook: Mapping[str, Any]) -> str:
    tool = str(hook.get("tool_name"))
    given = hook.get("tool_input")
    command = given.get("command") if isinstance(given, dict) else None
    if not isinstance(command, str):
        return tool
    return f"{tool}: {command[:MAX_SHOWN]}{'…' if len(command) > MAX_SHOWN else ''}"


def _redacted(text: str, secrets: Iterable[str]) -> str:
    for value in sorted(set(secrets), key=len, reverse=True):
        text = text.replace(value, "[secret]")
    return text


# --- install -----------------------------------------------------------------------------


def install(project: Path, python: str = sys.executable) -> list[Path]:
    """Write the hooks into the project's SETTINGS_FILE, and a rules file if there is none; return the files written.

    Other settings and other hooks are kept; this one's earlier hooks are replaced.

    Raises:
        WatchError: the settings file can't be read or isn't the shape Claude Code reads.
    """
    project = Path(project)
    settings_path = project / SETTINGS_FILE
    settings = _settings(settings_path)
    hooks = settings.setdefault("hooks", {})
    command = " ".join(
        shlex.quote(part)
        for part in (python, "-I", "-S", str(Path(watch_client.__file__)), "--project", str(project.resolve()))
    )
    for event in HOOK_EVENTS:
        entry: dict[str, Any] = {"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]}
        if event not in NO_MATCHER:
            entry = {"matcher": "*", **entry}
        hooks[event] = [*(e for e in hooks.get(event, []) if not _ours(e)), entry]
    settings_path.parent.mkdir(exist_ok=True)
    _write_json(settings_path, settings)
    written = [settings_path]
    rules_path = project / RULES_FILE
    if not rules_path.exists():
        source = next((s for s in DEFAULT_SOURCES if rule_missing(project, s, DEFAULT_RULE) is None), DEFAULT_SOURCES[0])
        rules_path.parent.mkdir(exist_ok=True)
        rule = {"source": source, "rule": DEFAULT_RULE, "check": LIVE_CHECKS[0]}
        _write_json(rules_path, {"version": 1, "rules": [rule]})
        written.append(rules_path)
    return written


def uninstall(project: Path) -> bool:
    """Remove this command's hooks from the project's SETTINGS_FILE; True if any were there."""
    settings_path = Path(project) / SETTINGS_FILE
    if not settings_path.exists():
        return False
    settings = _settings(settings_path)
    hooks = settings.pop("hooks", {})
    kept = {event: [entry for entry in entries if not _ours(entry)] for event, entries in hooks.items()}
    kept = {event: entries for event, entries in kept.items() if entries}
    if kept == hooks:
        return False
    _write_json(settings_path, {**settings, "hooks": kept} if kept else settings)
    return True


def _write_json(path: Path, value: object) -> None:
    """`value` as JSON indented by two spaces, as Claude Code's own settings files are, ending in a newline."""
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise WatchError(f"{path} isn't valid JSON; fix it or move it aside") from None
    hooks = settings.get("hooks", {}) if isinstance(settings, dict) else []
    if not isinstance(hooks, dict) or not all(isinstance(entries, list) for entries in hooks.values()):
        raise WatchError(f"{path} isn't the shape Claude Code reads: an object whose hooks map each event to a list")
    return settings


def _ours(entry: object) -> bool:
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    return isinstance(hooks, list) and any(isinstance(hook, dict) and "watch_client.py" in str(hook.get("command")) for hook in hooks)


# --- the socket ----------------------------------------------------------------------------


def prepare_socket(project: Path) -> str:
    """The watcher's socket path for `project`, its folder private to this user and no stale socket in the way.

    Raises:
        WatchError: the folder is open to others, or a watcher is already running for the project.
    """
    path = watch_client.socket_path(str(project))
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    info = os.stat(folder)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise WatchError(f"{folder} is a folder others can open, so the watcher's socket can't go there")
    if os.path.lexists(path):
        try:
            with socket.socket(socket.AF_UNIX) as conn:
                conn.connect(path)
        except OSError:
            os.unlink(path)
        else:
            raise WatchError(f"a watcher is already running for {project}")
    return path


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        line = self.rfile.readline(MAX_REQUEST)
        try:
            hook = json.loads(line)
        except ValueError:
            hook = {}
        reply = self.server.watcher.handle(hook) if isinstance(hook, dict) else {}
        self.wfile.write(json.dumps(reply).encode() + b"\n")


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def start_server(watcher: Watcher, path: str, on_alert: Callable[[str], None] = _ignore) -> _Server:
    """Serve `watcher` on the socket at `path`, in a background thread; call shutdown() and server_close() to stop.

    Raises:
        WatchError: the socket can't be made.
    """
    watcher.on_alert = on_alert
    try:
        server = _Server(path, _Handler)
    except OSError as error:
        raise WatchError(f"can't listen on {path}: {error.strerror or error}") from None
    server.watcher = watcher
    # A daemon thread, so a watcher that is never shut down can't keep Python from exiting.
    server.thread = threading.Thread(target=server.serve_forever, daemon=True)
    server.thread.start()
    return server


# --- the command ---------------------------------------------------------------------------


def command(action: str, project: Path, out: TextIO, python: str = sys.executable, stop: threading.Event | None = None) -> int:
    """Run `whitney watch <action>`: run (the default), install or uninstall. Returns the exit code."""
    project = Path(project)
    try:
        if action == "install":
            for path in install(project, python=python):
                out.write(f"wrote {path}\n")
            out.write(f"Start the watcher with: whitney watch --project {shlex.quote(str(project))}\n")
            out.write("Then start Claude Code in the project. Its SessionStart message says what is watched.\n")
            return 0
        if action == "uninstall":
            out.write("removed whitney watch's hooks\n" if uninstall(project) else "no whitney watch hooks to remove\n")
            return 0
        return run(project, out, stop or threading.Event())
    except WatchError as error:
        out.write(f"whitney watch: {error}\n")
        return 2


def run(project: Path, out: TextIO, stop: threading.Event) -> int:
    """Watch `project` until `stop` is set or Ctrl-C, printing each alert as it happens."""
    watcher = Watcher(project)
    path = prepare_socket(watcher.project)
    out.write(watcher.watching() + "\n")
    out.write(f"Listening on {path}. Ctrl-C to stop.\n")
    out.flush()

    def report(message: str) -> None:
        out.write(f"\n[{time.strftime('%H:%M:%S')}] {message}\n")
        out.flush()

    server = start_server(watcher, path, on_alert=report)
    try:
        stop.wait()
    except KeyboardInterrupt:
        out.write("stopped\n")
    finally:
        server.shutdown()
        server.server_close()
        if os.path.lexists(path):
            os.unlink(path)
    return 0
