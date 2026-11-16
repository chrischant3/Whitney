"""Spike (Task 66): whitney watch on a real Claude Code session, with a scripted model and no API key.

Claude Code runs for real, with the hooks `whitney watch install` writes and
the watcher running beside it. The model is a stand-in on 127.0.0.1 that
speaks the Messages API: asked with tools, it calls Bash once with the
scenario's command, then answers "Done.". So the run costs nothing, and the
stand-in keeps every request Claude Code sends it, which shows whether the
alert ever reached the model.

    python spikes/watch_live.py --out docs/results/watch-live [--claude claude]

It also times the hook (<out>/latency.json): the client against a running
watcher, against none, and a bare interpreter, plus git's cost on --big-repo.

For each scenario it writes <out>/<scenario>/: stream.jsonl (Claude Code's
stream-json with --include-hook-events), watcher.txt (the watcher's own
output), requests.json (each request the model was sent, summarised: whether it held any of whitney watch's text) and summary.json. The
fixture's .env holds fake canary values, and the summary records whether any
of them appear in anything saved.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

RULE = "Never commit .env files or secrets"
CANARIES = ("sk_live_spike_watch_9f3c2e1d", "pw_spike_watch_4b7a")
SCENARIOS = {
    "commits-env": "git add -A && git commit -q -m 'Save my work'",
    "commits-app-only": "git add app.py && git commit -q -m 'Update app'",
    # A Bash call that fails after committing: Claude Code runs PostToolUseFailure, not PostToolUse.
    "commits-env-then-fails": "git add -A && git commit -q -m 'Save my work' && exit 3",
}


class Model(http.server.BaseHTTPRequestHandler):
    """The scripted model: one Bash call for a request with tools and no tool result yet, else "Done."."""

    command = ""
    requests: list[dict] = []

    def log_message(self, *args):
        return None

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        Model.requests.append({"path": self.path, "body": body})
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            self._json({"input_tokens": 1} if "count_tokens" in self.path else {})
            return
        answered = any(
            isinstance(block, dict) and block.get("type") == "tool_result"
            for message in body.get("messages", [])
            if isinstance(message.get("content"), list)
            for block in message["content"]
        )
        if body.get("tools") and not answered:
            block = {"type": "tool_use", "id": f"toolu_spike_{len(Model.requests)}", "name": "Bash", "input": {"command": Model.command, "description": "Commit"}}
            stop = "tool_use"
        else:
            block = {"type": "text", "text": "Done."}
            stop = "end_turn"
        if body.get("stream"):
            self._stream(body.get("model", "claude"), block, stop)
        else:
            self._json({"id": "msg_spike", "type": "message", "role": "assistant", "model": body.get("model"), "content": [block],
                        "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})

    def _json(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, model, block, stop):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def send(kind, data):
            self.wfile.write(f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n".encode())

        send("message_start", {"message": {"id": "msg_spike", "type": "message", "role": "assistant", "model": model, "content": [],
                                           "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}})
        if block["type"] == "tool_use":
            send("content_block_start", {"index": 0, "content_block": {**block, "input": {}}})
            send("content_block_delta", {"index": 0, "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}})
        else:
            send("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
            send("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": block["text"]}})
        send("content_block_stop", {"index": 0})
        send("message_delta", {"delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 1}})
        send("message_stop", {})
        self.wfile.flush()


def project(folder: Path) -> Path:
    """The template's AGENTS.md, an app.py, one commit, and an uncommitted .env of fake canaries."""
    repo = folder / "project"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
    git = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=Spike", "-c", "user.email=spike@example.invalid", *a], check=True, capture_output=True, env=env)
    git("init", "-q", "-b", "main")
    shutil.copy(Path(__file__).resolve().parents[1] / "template" / "claude-code" / "AGENTS.md", repo / "AGENTS.md")
    (repo / "app.py").write_text("print('hello')\n")
    (repo / ".gitignore").write_text(".claude/settings.local.json\n")
    git("add", "-A")
    git("commit", "-q", "-m", "Initial version")
    (repo / "app.py").write_text("print('hello, world')\n")
    (repo / ".env").write_text(f"API_KEY={CANARIES[0]}\nDB_PASSWORD={CANARIES[1]}\n")
    return repo


def scenario(name: str, command: str, out: Path, claude: str, runtime: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        repo = project(Path(tmp))
        env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
               "GIT_AUTHOR_NAME": "Agent", "GIT_AUTHOR_EMAIL": "agent@example.invalid", "GIT_COMMITTER_NAME": "Agent",
               "GIT_COMMITTER_EMAIL": "agent@example.invalid"}
        whitney = [str(Path(sys.executable).with_name("whitney"))]
        subprocess.run([*whitney, "watch", "install", "--project", str(repo)], check=True, capture_output=True, env=env)
        watcher = subprocess.Popen([*whitney, "watch", "--project", str(repo)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True)
        deadline = time.monotonic() + 30
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
        from whitney.watch_client import socket_path

        while not os.path.exists(socket_path(str(repo))):
            if time.monotonic() > deadline or watcher.poll() is not None:
                raise SystemExit(f"the watcher didn't start: {watcher.communicate(timeout=5)[0]}")
            time.sleep(0.1)
        Model.command, Model.requests = command, []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # Only what Claude Code needs: the session's own CLAUDE_* and proxy variables (a user's email among them) stay out.
        kept = {name: os.environ[name] for name in ("PATH", "LANG", "TMPDIR") if name in os.environ}
        claude_env = {**kept, **{k: v for k, v in env.items() if k.startswith("GIT_") or k == "XDG_RUNTIME_DIR"}, "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}", "ANTHROPIC_API_KEY": "sk-ant-spike-not-a-key",
                      "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                      "DISABLE_AUTOUPDATER": "1", "HOME": str(Path(tmp) / "home")}
        (Path(tmp) / "home").mkdir()
        started = time.monotonic()
        done = subprocess.run(
            [claude, "-p", "Please commit my work.", "--output-format", "stream-json", "--verbose", "--include-hook-events",
             "--setting-sources", "project,local", "--allowedTools", "Bash", "--model", "claude-haiku-4-5"],
            cwd=repo, env=claude_env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300,
        )
        elapsed = time.monotonic() - started
        server.shutdown()
        watcher.terminate()
        watcher_out = watcher.communicate(timeout=10)[0]
        committed = subprocess.run(["git", "-C", str(repo), "log", "--name-only", "--format=%h %s"], capture_output=True, text=True).stdout
    (out / "stream.jsonl").write_text(done.stdout)
    (out / "stderr.txt").write_text(done.stderr)
    (out / "watcher.txt").write_text(watcher_out)
    # The requests hold Claude Code's whole system prompt; keep what they show, not the prompt.
    (out / "requests.json").write_text(json.dumps([
        {"path": r["path"], "model": r["body"].get("model"), "tools": len(r["body"].get("tools") or []),
         "messages": len(r["body"].get("messages") or []), "whitney_watch_text_in_request": "whitney watch" in json.dumps(r)}
        for r in Model.requests], indent=2) + "\n")
    saved = "".join(p.read_text() for p in out.iterdir() if p.is_file())
    hook_outputs = [e for e in map(json.loads, filter(None, done.stdout.splitlines())) if "hook" in str(e.get("subtype", "")) or "hook" in str(e.get("type", ""))]
    summary = {
        "scenario": name,
        "command": command,
        "claude_exit": done.returncode,
        "seconds": round(elapsed, 2),
        "git_log": committed,
        "alert_in_watcher_output": "a rule wasn't followed" in watcher_out,
        "rule_quoted_in_watcher_output": f'Rule: "{RULE}" (AGENTS.md)' in watcher_out,
        "alert_in_stream": "a rule wasn't followed" in done.stdout,
        "alert_sent_to_model": any("a rule wasn't followed" in json.dumps(r) for r in Model.requests),
        "watch_message_sent_to_model": any("whitney watch" in json.dumps(r) for r in Model.requests),
        "hook_events_in_stream": len(hook_outputs),
        "canary_in_saved_files": any(c in saved for c in CANARIES),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def timed(argv: list[str], stdin: str, env: dict, runs: int) -> list[float]:
    """Milliseconds each of `runs` runs of `argv` took, fed `stdin`."""
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, check=True)
        times.append((time.perf_counter() - started) * 1000)
    return times


def latency(out: Path, runtime: Path, big_repo: Path, runs: int = 30) -> dict:
    """What one hook costs a tool call: the client against a running watcher, beside a bare interpreter."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from whitney.watch import Repo
    from whitney.watch_client import __file__ as client
    from whitney.watch_client import socket_path

    with tempfile.TemporaryDirectory() as tmp:
        repo = project(Path(tmp))
        env = {**os.environ, "XDG_RUNTIME_DIR": str(runtime)}
        whitney = str(Path(sys.executable).with_name("whitney"))
        subprocess.run([whitney, "watch", "install", "--project", str(repo)], check=True, capture_output=True, env=env)
        watcher = subprocess.Popen([whitney, "watch", "--project", str(repo)], stdout=subprocess.DEVNULL, env=env)
        deadline = time.monotonic() + 30
        while not os.path.exists(socket_path(str(repo))):
            if time.monotonic() > deadline or watcher.poll() is not None:
                raise SystemExit("the watcher didn't start")
            time.sleep(0.1)
        hook = lambda event: json.dumps({"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_use_id": "t"})
        client_argv = [sys.executable, "-I", "-S", client, "--project", str(repo)]
        result = {
            "runs": runs,
            "bare_interpreter_ms": timed([sys.executable, "-I", "-S", "-c", "pass"], "", env, runs),
            "pre_tool_use_ms": timed(client_argv, hook("PreToolUse"), env, runs),
            "post_tool_use_ms": timed(client_argv, hook("PostToolUse"), env, runs),
            "no_watcher_ms": None,
        }
        watcher.terminate()
        watcher.wait(timeout=10)
        result["no_watcher_ms"] = timed(client_argv, hook("PostToolUse"), env, runs)
    big = Repo(big_repo)
    started = time.perf_counter()
    commits = big.commits()
    result["big_repo_commits"] = len(commits)
    result["big_repo_all_commits_ms"] = round((time.perf_counter() - started) * 1000, 1)
    result["big_repo_fingerprint_ms"] = timed_call(big.fingerprint, runs)
    summary = {key: (_stats(value) if isinstance(value, list) else value) for key, value in result.items()}
    (out / "latency.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def timed_call(function, runs: int) -> list[float]:
    times = []
    for _ in range(runs):
        started = time.perf_counter()
        function()
        times.append((time.perf_counter() - started) * 1000)
    return times


def _stats(times: list[float]) -> dict:
    ordered = sorted(times)
    return {"median": round(ordered[len(ordered) // 2], 1), "p90": round(ordered[int(len(ordered) * 0.9)], 1), "max": round(ordered[-1], 1)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--big-repo", default=str(Path(__file__).resolve().parents[1]), help="a large repository to time git against")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wr") as runtime:
        os.chmod(runtime, 0o700)
        # This process looks for the watcher's socket too, so it must agree with the watcher on where it is.
        os.environ["XDG_RUNTIME_DIR"] = runtime
        results = [scenario(name, command, Path(args.out) / name, args.claude, Path(runtime)) for name, command in SCENARIOS.items()]
        results.append(latency(Path(args.out), Path(runtime), Path(args.big_repo)))
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
