"""The hook side of `whitney watch`: hands one Claude Code hook event to the watcher and prints its alert.

Claude Code runs this as a command hook on SessionStart, PreToolUse,
PostToolUse, PostToolUseFailure and Stop (`whitney watch install` writes them). It is run as a script, by
path, so it imports nothing from Whitney or outside the standard library:
Python starts in about 14 ms, while importing Whitney's checks takes over
100 ms and its CLI over a second, on every tool call (tasks/plan-later.md,
Task 66).

It reads the hook's JSON on stdin, sends the fields the watcher needs over the
watcher's Unix socket, and prints the watcher's reply, which is either nothing
or {"systemMessage": ...}. Claude Code shows a synchronous hook's systemMessage
to the person, not to the model (code.claude.com/docs/en/hooks, read
2026-11-15), and this hook prints no other field, so it never steers the agent.
It always exits 0, and prints nothing on any failure but one: at SessionStart,
when no watcher answers, it says so, so nobody believes they are watched when
they aren't.

    python watch_client.py --project <dir>     # hook JSON on stdin
"""

import hashlib
import json
import os
import socket
import sys
import tempfile

# Seconds to wait for the watcher, end to end. A tool call waits this long at most.
TIMEOUT = 5.0
# The longest string kept from a tool's input; a Write tool's content can be a whole file.
MAX_STRING = 10_000
# The most bytes read from the watcher's reply.
MAX_REPLY = 64 * 1024
# A Unix socket's path must fit in about 104 bytes on macOS and 108 on Linux; leave room.
MAX_SOCKET_PATH = 100
# The hook fields the watcher reads.
FIELDS = ("hook_event_name", "session_id", "tool_name", "tool_input", "tool_use_id")
NOT_RUNNING = (
    "whitney watch isn't running for this project, so its rules aren't being watched. "
    "Start it with: whitney watch --project {project}"
)


def socket_path(project: str) -> str:
    """The watcher's socket for `project`, in a folder only this user can open.

    The name comes from the project's real path, so the hook and the watcher
    agree on it without being told. The folder is $XDG_RUNTIME_DIR, else a
    per-user folder in the temp folder, else one in /tmp, the first that keeps
    the path within MAX_SOCKET_PATH.
    """
    name = f"whitney-watch-{hashlib.sha256(os.path.realpath(project).encode()).hexdigest()[:16]}.sock"
    for folder in (os.environ.get("XDG_RUNTIME_DIR"), os.path.join(tempfile.gettempdir(), f"whitney-{os.getuid()}")):
        if folder and len(os.path.join(folder, name).encode()) <= MAX_SOCKET_PATH:
            return os.path.join(folder, name)
    return os.path.join("/tmp", f"whitney-{os.getuid()}", name)


def trimmed(value):
    """`value` with every string in it cut to MAX_STRING characters."""
    if isinstance(value, str):
        return value[:MAX_STRING]
    if isinstance(value, dict):
        return {key: trimmed(item) for key, item in value.items()}
    if isinstance(value, list):
        return [trimmed(item) for item in value]
    return value


def request(hook: dict) -> bytes:
    """The line sent to the watcher: the hook's FIELDS, trimmed."""
    return json.dumps({key: trimmed(hook[key]) for key in FIELDS if key in hook}).encode() + b"\n"


def ask(path: str, line: bytes, timeout: float = TIMEOUT) -> dict:
    """Send `line` to the watcher at `path` and return its reply.

    Raises:
        OSError: no watcher answered in time.
        ValueError: the reply wasn't a JSON object.
    """
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(timeout)
        conn.connect(path)
        conn.sendall(line)
        reply = b""
        while not reply.endswith(b"\n") and len(reply) < MAX_REPLY:
            chunk = conn.recv(MAX_REPLY)
            if not chunk:
                break
            reply += chunk
    answer = json.loads(reply)
    if not isinstance(answer, dict):
        raise ValueError("the watcher's reply wasn't a JSON object")
    return answer


def output(answer: dict) -> str:
    """What to print for the watcher's `answer`: its systemMessage alone, or nothing."""
    message = answer.get("systemMessage")
    return json.dumps({"systemMessage": message}) if isinstance(message, str) and message else ""


def main(argv: list[str], stdin, stdout) -> int:
    """Handle one hook event; always 0, so a broken watcher never breaks the session."""
    if len(argv) != 2 or argv[0] != "--project":
        return 0
    project = argv[1]
    try:
        hook = json.loads(stdin.read())
    except ValueError:
        return 0
    if not isinstance(hook, dict):
        return 0
    try:
        printed = output(ask(socket_path(project), request(hook)))
    except (OSError, ValueError):
        starting = hook.get("hook_event_name") == "SessionStart"
        printed = output({"systemMessage": NOT_RUNNING.format(project=project)}) if starting else ""
    if printed:
        stdout.write(printed + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))
