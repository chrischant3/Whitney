"""The hook client for whitney watch (whitney/watch_client.py), which Claude Code runs on every tool call."""

import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from whitney import watch_client

CLIENT = Path(watch_client.__file__)


def run_main(argv: list[str], stdin: str) -> tuple[int, str]:
    out = io.StringIO()
    code = watch_client.main(argv, io.StringIO(stdin), out)
    return code, out.getvalue()


class FakeWatcher:
    """A socket that records what it was sent and answers with `reply` (raw bytes)."""

    def __init__(self, path: str, reply: bytes):
        self.received = b""
        self.cut_off = False
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(1)
        self.reply = reply
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        with conn:
            while not self.received.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                self.received += chunk
            try:
                conn.sendall(self.reply)
            except BrokenPipeError:
                self.cut_off = True

    def close(self):
        self.thread.join(timeout=5)
        self.sock.close()


@pytest.fixture
def runtime(monkeypatch):
    """A private runtime folder with a short path, so a socket in it stays within the length a Unix socket allows."""
    import shutil
    import tempfile

    folder = Path(tempfile.mkdtemp(prefix="w", dir="/tmp"))
    folder.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(folder))
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def test_the_socket_path_depends_only_on_the_projects_real_path(runtime, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    link = tmp_path / "link"
    link.symlink_to(project)
    path = watch_client.socket_path(str(project))
    assert path == watch_client.socket_path(str(link))
    assert path != watch_client.socket_path(str(tmp_path))
    assert Path(path).parent == runtime
    assert Path(path).name.startswith("whitney-watch-") and len(Path(path).name) == len("whitney-watch-") + 16 + len(".sock")


def test_without_a_runtime_folder_the_socket_goes_in_a_per_user_temp_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(watch_client.tempfile, "gettempdir", lambda: "/var/tmp")
    assert Path(watch_client.socket_path(str(tmp_path))).parent == Path(f"/var/tmp/whitney-{os.getuid()}")


def test_the_request_keeps_only_the_fields_the_watcher_reads_with_long_strings_cut():
    hook = {
        "hook_event_name": "PostToolUse",
        "session_id": "s",
        "tool_name": "Write",
        "tool_input": {"file_path": "a.py", "content": "x" * 20_000, "lines": ["y" * 20_000, 3]},
        "tool_use_id": "t",
        "tool_response": {"big": "z" * 20_000},
        "transcript_path": "/home/someone/.claude/projects/x.jsonl",
    }
    sent = json.loads(watch_client.request(hook))
    assert set(sent) == {"hook_event_name", "session_id", "tool_name", "tool_input", "tool_use_id"}
    assert sent["tool_input"]["content"] == "x" * watch_client.MAX_STRING
    assert sent["tool_input"]["lines"] == ["y" * watch_client.MAX_STRING, 3]
    assert watch_client.request(hook).endswith(b"\n")


@pytest.mark.parametrize(
    "answer, printed",
    [
        ({"systemMessage": "hello"}, '{"systemMessage": "hello"}'),
        ({"systemMessage": "hello", "decision": "block", "hookSpecificOutput": {"additionalContext": "x"}}, '{"systemMessage": "hello"}'),
        ({"systemMessage": ""}, ""),
        ({"systemMessage": 3}, ""),
        ({}, ""),
    ],
)
def test_only_a_system_message_is_ever_printed(answer, printed):
    """Never a field that reaches the model, whatever the watcher sends: alerts only."""
    assert watch_client.output(answer) == printed


def test_the_watchers_alert_is_printed(runtime, tmp_path):
    fake = FakeWatcher(watch_client.socket_path(str(tmp_path)), b'{"systemMessage": "a rule wasn\'t followed"}\n')
    code, out = run_main(["--project", str(tmp_path)], json.dumps({"hook_event_name": "PostToolUse", "tool_use_id": "t"}))
    fake.close()
    assert code == 0
    assert json.loads(out) == {"systemMessage": "a rule wasn't followed"}
    assert json.loads(fake.received) == {"hook_event_name": "PostToolUse", "tool_use_id": "t"}


def test_a_reply_over_the_cap_is_cut_off_and_refused(runtime, tmp_path):
    path = watch_client.socket_path(str(tmp_path))
    fake = FakeWatcher(path, b'{"systemMessage": "' + b"m" * (watch_client.MAX_REPLY * 4) + b'"}\n')
    with pytest.raises(ValueError):
        watch_client.ask(path, b"{}\n")
    fake.close()


def test_a_reply_sent_in_pieces_is_read_whole(runtime, tmp_path):
    path = watch_client.socket_path(str(tmp_path))
    message = "m" * (watch_client.MAX_REPLY // 2)
    fake = FakeWatcher(path, json.dumps({"systemMessage": message}).encode() + b"\n")
    assert watch_client.ask(path, b"{}\n") == {"systemMessage": message}
    fake.close()


def test_a_reply_that_isnt_an_object_prints_nothing(runtime, tmp_path):
    fake = FakeWatcher(watch_client.socket_path(str(tmp_path)), b"[1]\n")
    assert run_main(["--project", str(tmp_path)], "{}") == (0, "")
    fake.close()


def test_a_reply_cut_off_without_a_newline_still_counts(runtime, tmp_path):
    fake = FakeWatcher(watch_client.socket_path(str(tmp_path)), b'{"systemMessage": "partial"}')
    assert json.loads(run_main(["--project", str(tmp_path)], "{}")[1]) == {"systemMessage": "partial"}
    fake.close()


def test_with_no_watcher_a_tool_call_hook_prints_nothing(runtime, tmp_path):
    assert run_main(["--project", str(tmp_path)], json.dumps({"hook_event_name": "PostToolUse"})) == (0, "")


def test_with_no_watcher_session_start_says_the_project_isnt_watched(runtime, tmp_path):
    code, out = run_main(["--project", str(tmp_path)], json.dumps({"hook_event_name": "SessionStart"}))
    assert code == 0
    assert json.loads(out) == {"systemMessage": watch_client.NOT_RUNNING.format(project=tmp_path)}
    assert "isn't running" in out


@pytest.mark.parametrize("argv", [[], ["--project"], ["--dir", "x"], ["--project", "a", "b"]])
def test_bad_arguments_exit_0_silently(argv):
    assert run_main(argv, "{}") == (0, "")


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", "3"])
def test_bad_input_exits_0_silently(runtime, tmp_path, stdin):
    assert run_main(["--project", str(tmp_path)], stdin) == (0, "")


def test_a_watcher_that_never_answers_is_given_up_on(runtime, tmp_path):
    path = watch_client.socket_path(str(tmp_path))
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    started = time.monotonic()
    with pytest.raises(OSError):
        watch_client.ask(path, b"{}\n", timeout=0.3)
    assert time.monotonic() - started < 2
    listener.close()


def test_as_a_script_it_runs_with_only_the_standard_library(runtime, tmp_path):
    """Claude Code runs it as `python -I -S watch_client.py`: no site-packages, no Whitney package."""
    done = subprocess.run(
        [sys.executable, "-I", "-S", str(CLIENT), "--project", str(tmp_path)],
        input=json.dumps({"hook_event_name": "SessionStart"}),
        capture_output=True,
        text=True,
        env={**os.environ, "XDG_RUNTIME_DIR": str(runtime)},
        timeout=30,
    )
    assert done.returncode == 0
    assert "isn't running" in json.loads(done.stdout)["systemMessage"]
    assert done.stderr == ""


def test_a_runtime_folder_too_long_for_a_socket_falls_back_to_the_temp_folder(monkeypatch, tmp_path):
    long = tmp_path / ("x" * 120)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(long))
    monkeypatch.setattr(watch_client.tempfile, "gettempdir", lambda: "/tmp")
    path = watch_client.socket_path(str(tmp_path))
    assert path.startswith(f"/tmp/whitney-{os.getuid()}/whitney-watch-")
    assert len(path.encode()) <= watch_client.MAX_SOCKET_PATH


def test_a_temp_folder_too_long_too_falls_back_to_tmp(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(watch_client.tempfile, "gettempdir", lambda: "/" + "y" * 120)
    assert watch_client.socket_path(str(tmp_path)).startswith(f"/tmp/whitney-{os.getuid()}/whitney-watch-")


def test_a_path_exactly_at_the_limit_is_kept(monkeypatch, tmp_path):
    name_length = len("/whitney-watch-") + 16 + len(".sock")
    folder = "/" + "z" * (watch_client.MAX_SOCKET_PATH - name_length - 1)
    monkeypatch.setenv("XDG_RUNTIME_DIR", folder)
    path = watch_client.socket_path(str(tmp_path))
    assert path.startswith(folder + "/") and len(path) == watch_client.MAX_SOCKET_PATH
    monkeypatch.setenv("XDG_RUNTIME_DIR", folder + "z")
    assert not watch_client.socket_path(str(tmp_path)).startswith(folder)
