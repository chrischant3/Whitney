"""Spike, Phase 5 Task 31: what was sent to the model, and the agent's reasoning.

Each case stages a small git project, runs Kilo or Claude Code once through
Whitney's own runner (the same temporary HOME, environment allow-list, git
isolation and redaction as an eval), and captures what the harness sent the
model in one or more of these ways:

- a Kilo plugin on the chat hooks, loaded from the KILO_CONFIG overlay's
  `plugin` list, which needs `kilo run` without `--pure`;
- a logging proxy set as the provider's base URL (Kilo) or as
  ANTHROPIC_BASE_URL (Claude Code), which forwards every request and records
  its path, its header *names* and its body, never a header's value;
- Claude Code's own `OTEL_LOG_RAW_API_BODIES=file:<dir>`, which writes each
  request and response body to disk, its OpenTelemetry logs sent to a local
  sink that keeps nothing;
- Inspect's own log, for Claude Code in Whitney's Docker sandbox, whose
  model calls go through Inspect's bridge.

Run from the repo root. Cases marked "no model" run `kilo debug config` and
call no model. The others call Haiku once or twice, about a cent each:
ANTHROPIC_API_KEY must be set, and Kilo's Gateway is reached through the
user's Kilo login.

    .venv/bin/python spikes/prompt_capture.py --list
    .venv/bin/python spikes/prompt_capture.py --out /tmp/whitney-t31 --case kilo-plugin-sources
    .venv/bin/python spikes/prompt_capture.py --out /tmp/whitney-t31 --analyse
    .venv/bin/python spikes/prompt_capture.py --out /tmp/whitney-t31 --fixtures tests/data/prompt-capture

Raw output stays in --out, outside the repo; it holds unredacted plugin and
request captures, so treat it as you would a log. Only --fixtures output is
meant to be committed, and it refuses to write a file that still holds a
local path, an `sk-` key, the user's name or an email address.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import http.client
import json
import os
import shutil
import tempfile
import threading
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import claude_code_shapes
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser
from run_to_fixture import check_nothing_left
from skill_agent_shapes import BASE_FILES, _home_with, _init_repo, _kilo_version, sanitise, sanitise_emails

import whitney.agent as kilo_agent
import whitney.claude_code as claude_code
from whitney.tasks import DEFAULT_HARNESS_PERMISSIONS

REPO = Path(__file__).resolve().parents[1]
TIMEOUT = 180
GATEWAY_MODEL = "kilo/anthropic/claude-haiku-4.5"
KEY_MODEL = "anthropic/claude-haiku-4-5"
CLAUDE_MODEL = "claude-haiku-4-5"
ANTHROPIC_UPSTREAM = "https://api.anthropic.com"
GATEWAY_UPSTREAM = "https://api.kilo.ai"
GATEWAY_PATH = "/api/gateway"  # credentials.DEFAULT_GATEWAY_URL's path, kept so the proxy forwards to the same place
# 2026-11-12, spikes/capture_followups.py: wrong for the proxy. With this path Kilo's model list got 405 and its chat
# call fell back to the OpenAI format at /api/openrouter/chat/completions; with the origin alone the list loads and the
# chat call is Anthropic's format at /api/openrouter/messages, as without a proxy. Use the origin alone.
# Headers the proxy doesn't pass on: hop-by-hop ones, and those it sets itself.
HOP_HEADERS = {"host", "connection", "keep-alive", "transfer-encoding", "content-length", "accept-encoding", "proxy-connection"}
CAPTURE_PLUGIN_ID = "whitney-capture"

# One rule file, one skill and one permission rule per target, each with a marker, so a capture shows which reached the model.
AGENTS_MD = "# Project rules\n\n- Never commit .env files or secrets.\n- Rule marker: CAPTURE-RULE-7319\n"
SKILL_DESCRIPTION = "Write release notes for this project. Use when the user asks for release notes or a changelog entry."
SKILL_MD = f"---\nname: release-notes\ndescription: {SKILL_DESCRIPTION}\n---\n\n# Release notes\n\nSkill body marker: CAPTURE-SKILL-BODY-2287\n"
KILO_JSONC = '{\n  "permission": {\n    "external_directory": "ask",\n    "bash": {"git push*": "deny"}\n  }\n}\n'
CLAUDE_SETTINGS = '{\n  "permissions": {\n    "deny": ["Bash(git push:*)"]\n  }\n}\n'
KILO_FILES = {"AGENTS.md": AGENTS_MD, ".kilo/skills/release-notes/SKILL.md": SKILL_MD, "kilo.jsonc": KILO_JSONC}
CLAUDE_FILES = {"AGENTS.md": AGENTS_MD, ".claude/skills/release-notes/SKILL.md": SKILL_MD, ".claude/settings.json": CLAUDE_SETTINGS}
PONG = "Reply with the single word PONG and nothing else."
SUM = "What is 17 + 25? Think it through briefly, then reply with the number only."

# A plugin that writes what each chat hook is given to a file. It does nothing else and changes nothing.
CAPTURE_PLUGIN = """import { appendFileSync } from "node:fs"
const OUT = %s
const safe = (value) => { const seen = new WeakSet(); return JSON.stringify(value, (k, v) => {
  if (typeof v === "object" && v !== null) { if (seen.has(v)) return "[circular]"; seen.add(v) }
  return typeof v === "function" ? "[function]" : v }) }
const log = (hook, input, output) => appendFileSync(OUT, safe({ hook, input, output }) + "\\n")
export default {
  id: "%s",
  server: async () => {
    log("server", null, null)
    return {
      "experimental.chat.system.transform": async (input, output) => log("system", input, output),
      "experimental.chat.messages.transform": async (input, output) => log("messages", input, output),
      "chat.params": async (input, output) => log("params", input, output),
    }
  },
}
"""
# The no-model probe's plugins, one per place Kilo looks, each leaving a marker when its server function runs.
MARKER_PLUGIN = """import { appendFileSync } from "node:fs"
export default { id: "%s", server: async () => { appendFileSync(%s, "ran\\n"); return {} } }
"""


@dataclass
class Case:
    """One run to capture.

    Attributes:
        id: names the output folder.
        about: one line for the write-up.
        target: "kilo", "claude-code", "claude-code-docker" or "kilo-debug" (no model).
        prompt: the message.
        files: project files on top of BASE_FILES.
        model: the model the harness is told.
        plugin: Kilo: load the capture plugin from the overlay, and drop --pure.
        proxy: the upstream the logging proxy forwards to, or None for no proxy.
        options: extra harness options, before the prompt.
        otel_bodies: Claude Code: set OTEL_LOG_RAW_API_BODIES=file:<dir>.
        thinking_settings: Claude Code: keys added to the --settings JSON.
        env: extra environment for the harness.
    """

    id: str
    about: str
    target: str
    prompt: str = PONG
    files: dict[str, str] = field(default_factory=dict)
    model: str = ""
    plugin: bool = False
    proxy: str | None = None
    options: list[str] = field(default_factory=list)
    otel_bodies: bool = False
    thinking_settings: dict = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


CASES = [
    Case("kilo-plugin-sources", "No model: a plugin in the temporary HOME, the overlay and the project; which run with and without --pure",
         "kilo-debug", files=KILO_FILES),
    Case("kilo-plugin-overlay-only", "No model: Whitney's plugin in the overlay only, the project with no plugin folder; what else Kilo loads and writes",
         "kilo-debug", files=KILO_FILES),
    Case("kilo-gateway", "Kilo Gateway route, no --pure, the capture plugin, and the kilo provider's baseURL pointed at the proxy",
         "kilo", files=KILO_FILES, model=GATEWAY_MODEL, plugin=True, proxy=GATEWAY_UPSTREAM),
    Case("kilo-key", "Provider key route (ANTHROPIC_API_KEY), no --pure, the capture plugin and the proxy as anthropic's baseURL",
         "kilo", files=KILO_FILES, model=KEY_MODEL, plugin=True, proxy=ANTHROPIC_UPSTREAM),
    Case("kilo-thinking", "Kilo Gateway route with --variant high --thinking, the capture plugin and the proxy",
         "kilo", prompt=SUM, files=KILO_FILES, model=GATEWAY_MODEL, plugin=True, proxy=GATEWAY_UPSTREAM,
         options=["--variant", "high", "--thinking"]),
    Case("kilo-variant-only", "Kilo Gateway route with --pure kept, --variant high and no --thinking, the proxy only",
         "kilo", prompt=SUM, files=KILO_FILES, model=GATEWAY_MODEL, proxy=GATEWAY_UPSTREAM, options=["--variant", "high"]),
    Case("cc-local", "Claude Code, local: the proxy through ANTHROPIC_BASE_URL and OTEL_LOG_RAW_API_BODIES in the same run",
         "claude-code", files=CLAUDE_FILES, model=CLAUDE_MODEL, proxy=ANTHROPIC_UPSTREAM, otel_bodies=True),
    Case("cc-thinking", "Claude Code, local, thinking on (alwaysThinkingEnabled, showThinkingSummaries, MAX_THINKING_TOKENS), proxy and OTEL bodies",
         "claude-code", prompt=SUM, files=CLAUDE_FILES, model=CLAUDE_MODEL, proxy=ANTHROPIC_UPSTREAM, otel_bodies=True,
         thinking_settings={"alwaysThinkingEnabled": True, "showThinkingSummaries": True}, env={"MAX_THINKING_TOKENS": "2048"}),
    Case("cc-summaries-only", "Claude Code, local, showThinkingSummaries only (no budget, no alwaysThinkingEnabled); OTEL bodies only",
         "claude-code", prompt=SUM, files=CLAUDE_FILES, model=CLAUDE_MODEL, otel_bodies=True,
         thinking_settings={"showThinkingSummaries": True}),
    Case("cc-claude-md-import", "Claude Code, local: a CLAUDE.md that imports @AGENTS.md beside it; OTEL bodies only, no proxy",
         "claude-code", files={**CLAUDE_FILES, "CLAUDE.md": "@AGENTS.md\n"}, model=CLAUDE_MODEL, otel_bodies=True),
    Case("cc-docker", "Claude Code in Whitney's Docker sandbox through Inspect's bridge; what Inspect's log holds",
         "claude-code-docker", files=CLAUDE_FILES, model="anthropic/" + CLAUDE_MODEL),
]


class _Recorder:
    """Where a proxy writes its records, numbered in the order requests arrive."""

    def __init__(self, folder: Path, upstream: str | None):
        self.folder, self.upstream, self.count, self.lock = folder, upstream, 0, threading.Lock()
        folder.mkdir(parents=True, exist_ok=True)

    def next_number(self) -> int:
        with self.lock:
            self.count += 1
            return self.count


def _handler(recorder: _Recorder):
    class Handler(BaseHTTPRequestHandler):
        """Forward one request to the upstream and record it; with no upstream, answer 200 and record only the path."""

        def log_message(self, *args):  # the default writes every request to stderr
            pass

        def _handle(self):
            body = self.rfile.read(int(self.headers.get("content-length") or 0))
            number = recorder.next_number()
            if recorder.upstream is None:  # an OpenTelemetry sink: nothing of the export is kept
                (recorder.folder / f"{number:03}.sink.json").write_text(json.dumps({"path": self.path, "bytes": len(body)}))
                self._reply(200, {"content-type": "application/json"}, b"{}")
                return
            upstream = urllib.parse.urlsplit(recorder.upstream)
            connection = http.client.HTTPSConnection(upstream.netloc, timeout=TIMEOUT)
            headers = {name: value for name, value in self.headers.items() if name.lower() not in HOP_HEADERS}
            headers["accept-encoding"] = "identity"
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            answer = response.read()
            record = {
                "method": self.command,
                "path": self.path,
                "header_names": sorted({name.lower() for name in self.headers}),  # names only: values hold the key
                "body_bytes": len(body),
                "body": _json_or_text(body),
                "status": response.status,
                "response_header_names": sorted({name.lower() for name, _ in response.getheaders()}),
            }
            (recorder.folder / f"{number:03}.request.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
            (recorder.folder / f"{number:03}.response.txt").write_text(answer.decode(errors="replace"), encoding="utf-8")
            passed = {n: v for n, v in response.getheaders() if n.lower() not in HOP_HEADERS | {"content-encoding"}}
            self._reply(response.status, passed, answer)

        def _reply(self, status: int, headers: dict, payload: bytes):
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_POST = do_GET = _handle

    return Handler


def _json_or_text(body: bytes):
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body.decode(errors="replace")


@contextlib.contextmanager
def logging_proxy(folder: Path, upstream: str | None) -> Iterator[str]:
    """A local HTTP proxy to `upstream`, recording into `folder`; yields its base URL (http://127.0.0.1:<port>)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(_Recorder(folder, upstream)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()


def _write_plugin(folder: Path, plugin_id: str, marker: Path | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{plugin_id}.js"
    text = MARKER_PLUGIN % (plugin_id, json.dumps(str(marker))) if marker else CAPTURE_PLUGIN % (json.dumps(str(folder.parent / "plugin-capture.jsonl")), plugin_id)
    path.write_text(text, encoding="utf-8")
    return path


def _command_without_pure(case: Case, seen: dict, pure: bool):
    """A replacement for whitney.agent._kilo_command: --pure dropped unless `pure`, and the case's options added."""
    original = kilo_agent._kilo_command

    def command(state, model, auto, **named):
        argv = original(state, model, auto, **named)
        if not pure:
            argv.remove("--pure")
        end = argv.index("--")
        argv[end:end] = case.options
        if "--auto" in argv:
            raise ValueError("--auto turns every ask into allow; the spikes never pass it")
        seen["argv"] = argv
        return argv

    return command


def _snapshot(root: Path) -> list[str]:
    """Every file under `root`, relative, leaving out node_modules' contents, which are counted instead."""
    found, modules = [], 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "node_modules" in relative.parts[:-1]:
            modules += 1
        elif path.is_file() or path.name == "node_modules":
            found.append(str(relative))
    return [*found, f"(node_modules files: {modules})"]


def run_kilo_debug(case: Case, folder: Path) -> dict:
    """No model: `kilo debug config` with and without --pure, plugins placed as the case says; which plugins ran."""
    markers = folder / "markers"
    markers.mkdir(parents=True, exist_ok=True)
    places = ["overlay"] if case.id == "kilo-plugin-overlay-only" else ["overlay", "home", "project"]
    results = {}
    for pure in (True, False):
        for marker in markers.iterdir():
            marker.unlink()
        project_files = dict(BASE_FILES | case.files)
        home_files = {}
        if "project" in places:
            project_files[".kilo/plugin/project-plugin.js"] = MARKER_PLUGIN % ("project-plugin", json.dumps(str(markers / "project")))
        if "home" in places:
            home_files[".config/kilo/plugin/home-plugin.js"] = MARKER_PLUGIN % ("home-plugin", json.dumps(str(markers / "home")))
        overlay_plugin = _write_plugin(folder / "overlay-plugin", "overlay-plugin", markers / "overlay")
        overlay = {"plugin": [overlay_plugin.as_uri()]}
        seen: dict = {}
        state = AgentState(messages=[ChatMessageUser(content="unused")])
        with (
            kilo_agent.staged_project(project_files) as project,
            tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as config,
            _home_with(home_files, seen),
            mock.patch.object(kilo_agent, "_kilo_command", lambda *a, **k: ["kilo", "debug", "config", *(["--pure"] if pure else [])]),
            mock.patch.object(kilo_agent, "run_process", _keep_home_listing(seen)),
        ):
            _init_repo(project)
            json.dump(overlay, config)
            config.flush()
            run = asyncio.run(kilo_agent._run_local_process(project, state, "unused", False, {"KILO_CONFIG": config.name}, TIMEOUT))
            written = _snapshot(project)
        try:
            listed = json.loads(run["events"][run["events"].find("{"):]).get("plugin")
        except json.JSONDecodeError:
            listed = None
        results["pure" if pure else "not_pure"] = {
            "exit_code": run["exit_code"],
            "plugins_listed": [str(p).rsplit("/", 1)[-1] for p in listed or []],
            "plugins_ran": sorted(marker.name for marker in markers.iterdir()),
            "project_files_after": written,
            "home_files_after": seen.get("home_listing"),
        }
    return {"places": places, "results": results}


def _keep_home_listing(seen: dict):
    """A replacement for whitney.agent.run_process that lists the temporary HOME's files before it is removed."""
    original = kilo_agent.run_process

    async def run(command, cwd, env, timeout, name, stdin=None):
        result = await original(command, cwd, env, timeout, name, stdin)
        seen["home_listing"] = _snapshot(Path(env["HOME"]))
        return result

    return run


def run_kilo(case: Case, folder: Path) -> dict:
    """One `kilo run` through Whitney's runner, with the plugin, the proxy, or both."""
    seen: dict = {}
    api_key_env = "ANTHROPIC_API_KEY" if case.model.startswith("anthropic/") else None
    with contextlib.ExitStack() as stack:
        base_url = None
        if case.proxy:
            base_url = stack.enter_context(logging_proxy(folder / "proxy", case.proxy))
            base_url += GATEWAY_PATH if case.proxy == GATEWAY_UPSTREAM else "/v1"
        overlay = kilo_agent.local_config_overlay(
            case.model, DEFAULT_HARNESS_PERMISSIONS["kilo"], api_key_env, base_url, global_provider=kilo_agent.global_provider_settings()
        )
        if case.plugin:
            overlay["plugin"] = [_write_plugin(folder / "plugin", CAPTURE_PLUGIN_ID).as_uri()]
        state = AgentState(messages=[ChatMessageUser(content=case.prompt)])
        project = stack.enter_context(kilo_agent.staged_project({**BASE_FILES, **case.files}))
        config = stack.enter_context(tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8"))
        stack.enter_context(_home_with({}, seen))
        stack.enter_context(mock.patch.object(kilo_agent, "_kilo_command", _command_without_pure(case, seen, pure=not case.plugin)))
        _init_repo(project)
        json.dump(overlay, config)
        config.flush()
        env = {"KILO_CONFIG": config.name, **kilo_agent.referenced_env(overlay)}
        run = asyncio.run(kilo_agent._run_local_process(project, state, case.model, False, env, TIMEOUT))
        project_dir = str(project)
        written = _snapshot(project)
    return {
        "kilo_version": _kilo_version(),
        "argv": seen.get("argv"),
        "overlay_keys": sorted(overlay),
        "exit_code": run["exit_code"],
        "events": run["events"],
        "stderr": run["stderr"],
        "project_dir": project_dir,
        "home_dir": seen.get("home"),
        "project_files_after": written,
    }


def _claude_env(case: Case, folder: Path, extra: dict):
    """A replacement for whitney.claude_code.run_process: adds `extra` to the environment, and copies the session transcript out of the temporary HOME."""
    original = claude_code.run_process

    async def run(command, cwd, env, timeout, name, stdin=None):
        result = await original(command, cwd, {**env, **extra}, timeout, name, stdin)
        transcripts = folder / "transcripts"
        for path in Path(env["HOME"]).glob(".claude/projects/*/*.jsonl"):
            transcripts.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, transcripts / path.name)
        return result

    return run


def run_claude(case: Case, folder: Path) -> dict:
    """One `claude -p` through Whitney's runner, with the proxy, OTEL bodies, or both."""
    permissions = DEFAULT_HARNESS_PERMISSIONS["claude-code"]
    argv = [*claude_code._claude_command(case.model, permissions), "--", case.prompt]
    if case.thinking_settings:
        at = argv.index("--settings") + 1
        argv[at] = json.dumps({**json.loads(argv[at]), **case.thinking_settings})
    with contextlib.ExitStack() as stack:
        extra = dict(case.env)
        if case.proxy:
            extra["ANTHROPIC_BASE_URL"] = stack.enter_context(logging_proxy(folder / "proxy", case.proxy))
        if case.otel_bodies:
            sink = stack.enter_context(logging_proxy(folder / "otel-sink", None))
            extra |= {
                "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                "OTEL_LOGS_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
                "OTEL_EXPORTER_OTLP_ENDPOINT": sink,
                "OTEL_LOG_RAW_API_BODIES": f"file:{folder / 'otel-bodies'}",
            }
        stack.enter_context(mock.patch.object(claude_code, "run_process", _claude_env(case, folder, extra)))
        project = stack.enter_context(kilo_agent.staged_project({**BASE_FILES, **case.files}))
        _init_repo(project)
        run = asyncio.run(claude_code._run(argv, project, os.environ["ANTHROPIC_API_KEY"], TIMEOUT))
        project_dir = str(project)
    return {
        "claude_code_version": claude_code_shapes._claude_version(),
        "argv": argv,
        "env_added": sorted(extra),
        "exit_code": run["exit_code"],
        "events": run["events"],
        "stderr": run["stderr"],
        "project_dir": project_dir,
    }


def run_claude_docker(case: Case, folder: Path) -> dict:
    """Whitney's claude_code agent in its Docker sandbox, one sample, the Inspect log kept in `folder`/logs."""
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.scorer import includes

    files = {f"{kilo_agent.PROJECT_DIR}/{name}": text for name, text in {**BASE_FILES, **case.files}.items()}
    task = Task(
        dataset=[Sample(id=case.id, input=case.prompt, target="PONG", files=files, setup=f"git -C {kilo_agent.PROJECT_DIR} init -q")],
        solver=claude_code.claude_code(case.model, DEFAULT_HARNESS_PERMISSIONS["claude-code"], api_key_env=None, execution="docker"),
        scorer=includes(),
        sandbox=("docker", str(claude_code.COMPOSE_FILE)),
    )
    [log] = eval(task, model=case.model, log_dir=str(folder / "logs"), display="none")
    sample = log.samples[0]
    run = sample.store.get(kilo_agent.RUN_STORE_KEY) or {}
    return {"status": log.status, "exit_code": run.get("exit_code"), "events": run.get("events", ""), "stderr": run.get("stderr", ""),
            "harness_version": run.get("harness_version"), "log": str(next((folder / "logs").glob("*.eval")))}


def run_case(case: Case, out: Path) -> dict:
    """Run one case and save its raw output in out/<case id>/; return the summary."""
    folder = out / case.id
    if folder.exists():
        raise SystemExit(f"{folder} exists; every case is run once, so remove it by hand to run it again")
    folder.mkdir(parents=True)
    runner = {"kilo-debug": run_kilo_debug, "kilo": run_kilo, "claude-code": run_claude, "claude-code-docker": run_claude_docker}[case.target]
    result = runner(case, folder)
    events, stderr = result.pop("events", ""), result.pop("stderr", "")
    summary = {"case": case.id, "about": case.about, "target": case.target, "date": date.today().isoformat(),
               "model": case.model or None, "prompt": case.prompt, "project_files": {**BASE_FILES, **case.files}, **result}
    (folder / "events.jsonl").write_text(events, encoding="utf-8")
    (folder / "stderr.txt").write_text(stderr, encoding="utf-8")
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


# ---- analysis (no model) ----


def captured_requests(folder: Path) -> list[dict]:
    """The case's captured request bodies, from the proxy or from OTEL file mode, in order, each with where it came from."""
    found = []
    for path in sorted((folder / "proxy").glob("*.request.json")):
        record = json.loads(path.read_text())
        found.append({"via": "proxy", "file": path.name, "body": record["body"], "path": record["path"],
                      "header_names": record["header_names"], "bytes": record["body_bytes"]})
    for path in sorted((folder / "otel-bodies").glob("*.request.json"), key=lambda p: p.stat().st_mtime):
        found.append({"via": "otel", "file": path.name, "body": json.loads(path.read_text()), "bytes": path.stat().st_size})
    return found


def _text_of(value) -> str:
    """Every string in a JSON value, joined by newlines: where a request's text can be searched whatever its shape."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_text_of(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_text_of(v) for v in value)
    return ""


def normalised(text: str) -> str:
    """`text` with line endings made \\n and each line's trailing spaces and the whole text's outer blank lines removed."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip()


def found_in(source_text: str, request_text: str) -> dict:
    """Whether `source_text` is in `request_text`: exactly, after normalisation, and line by line."""
    lines = [line for line in normalised(source_text).split("\n") if line.strip()]
    hay = normalised(request_text)
    return {"exact": source_text in request_text, "normalised": normalised(source_text) in hay,
            "lines_found": sum(line in hay for line in lines), "lines": len(lines)}


def analyse_request(body: dict, files: dict[str, str]) -> dict:
    """What one request body holds: sizes, tools, thinking settings, and which project file's text it carries."""
    system = body.get("system")
    messages = body.get("messages") or []
    if isinstance(body.get("messages"), list) and not system:  # OpenAI format: the system prompt is a message
        system = [m for m in messages if m.get("role") == "system"]
    tools = body.get("tools") or []
    text = _text_of({k: v for k, v in body.items() if k != "tools"})
    return {
        "keys": sorted(body),
        "bytes_json": len(json.dumps(body)),
        "system_chars": len(_text_of(system)),
        "messages": len(messages),
        "message_chars": len(_text_of(messages)),
        "tools": len(tools),
        "tool_chars": len(json.dumps(tools)),
        "tool_names": [t.get("name") or (t.get("function") or {}).get("name") for t in tools],
        "thinking_params": {k: body[k] for k in ("thinking", "reasoning", "reasoning_effort", "output_config", "max_tokens") if k in body},
        "sources": {name: found_in(content, text) for name, content in files.items() if name not in BASE_FILES},
        "skill_description_found": SKILL_DESCRIPTION in text,
        "skill_body_marker_found": "CAPTURE-SKILL-BODY-2287" in text,
        "rule_marker_found": "CAPTURE-RULE-7319" in text,
        "where_rule_marker": _where(body, "CAPTURE-RULE-7319"),
    }


def _where(value, needle: str, path: str = "") -> list[str]:
    """The JSON paths of the strings in `value` that contain `needle`."""
    if isinstance(value, str):
        return [path or "."] if needle in value else []
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _where(v, needle, f"{path}/{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _where(v, needle, f"{path}/{i}")]
    return []


def secrets_seen(folder: Path) -> dict:
    """How many times each kind of secret this machine holds appears in the case's raw captures; counts only, never values."""
    secrets = {"ANTHROPIC_API_KEY": [os.environ.get("ANTHROPIC_API_KEY", "")], "kilo login": kilo_agent.login_secrets()}
    counts = {}
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl", ".txt"}):
        text = path.read_text(errors="replace")
        for kind, values in secrets.items():
            hits = sum(text.count(v) for v in values if len(v) >= kilo_agent.MIN_SECRET_LENGTH)
            if hits:
                counts[f"{path.relative_to(folder)}: {kind}"] = hits
    return counts


def thinking_seen(folder: Path) -> dict:
    """Where thinking text appears for the case: the harness's stream, the proxy's responses, OTEL response bodies, the transcript."""
    def count(paths, needles):
        hits = {}
        for path in paths:
            text = path.read_text(errors="replace")
            for needle in needles:
                if needle in text:
                    hits[needle] = hits.get(needle, 0) + text.count(needle)
        return hits

    needles = ['"type":"thinking"', '"type": "thinking"', '"thinking":"', '"thinking": "', "thinking_delta", "reasoning",
               '"type":"reasoning"', "reasoning_content", "redacted_thinking", '"signature"']
    return {
        "stream": count([folder / "events.jsonl"], needles),
        "proxy_responses": count(sorted((folder / "proxy").glob("*.response.txt")), needles),
        "otel_responses": count(sorted((folder / "otel-bodies").glob("*.response.json")), needles),
        "transcripts": count(sorted((folder / "transcripts").glob("*.jsonl")), needles),
        "plugin": count([p for p in [folder / "plugin-capture.jsonl"] if p.is_file()], needles),
    }


def analyse_plugin(folder: Path, files: dict[str, str]) -> dict | None:
    """What the capture plugin saw: hooks called, the system prompt's size, and which project files' text it carries."""
    path = folder / "plugin-capture.jsonl"
    if not path.is_file():
        return None
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    systems = [r for r in records if r["hook"] == "system"]
    system_text = _text_of(systems[0]["output"]) if systems else ""
    messages = [r for r in records if r["hook"] == "messages"]
    params = [r for r in records if r["hook"] == "params"]
    return {
        "hooks": [r["hook"] for r in records],
        "system_input_keys": sorted((systems[0]["input"] or {}).keys()) if systems else [],
        "system_output_keys": sorted((systems[0]["output"] or {}).keys()) if systems else [],
        "system_chars": len(system_text),
        "messages_output_keys": sorted((messages[0]["output"] or {}).keys()) if messages else [],
        "params_input_keys": sorted((params[0]["input"] or {}).keys()) if params else [],
        "params_output": params[0]["output"] if params else None,
        "tools_key_seen": any('"tools"' in json.dumps([r.get("input"), r.get("output")]) for r in records),
        "sources": {name: found_in(content, system_text + "\n" + _text_of([m["output"] for m in messages[:1]]))
                    for name, content in files.items() if name not in BASE_FILES},
        "plugin_bytes": path.stat().st_size,
    }


def analyse(out: Path) -> dict:
    """Read every saved case in `out` and write out/analysis.json."""
    report = {}
    for case in CASES:
        folder = out / case.id
        if not (folder / "summary.json").is_file():
            continue
        summary = json.loads((folder / "summary.json").read_text())
        files = summary["project_files"]
        requests = captured_requests(folder)
        entry = {
            "exit_code": summary.get("exit_code"),
            "requests": [{"via": r["via"], "file": r["file"], "path": r.get("path"), "header_names": r.get("header_names"),
                          "bytes": r["bytes"], **(analyse_request(r["body"], files) if isinstance(r["body"], dict) else {})} for r in requests],
            "plugin": analyse_plugin(folder, files),
            "thinking": thinking_seen(folder),
            "secrets_in_raw_captures": secrets_seen(folder),
            "stream_event_types": sorted({json.loads(l).get("type", "?") for l in (folder / "events.jsonl").read_text().splitlines() if l.startswith("{")}),
        }
        if case.target == "kilo-debug":
            entry = {k: summary[k] for k in ("places", "results")}
        if case.target == "claude-code-docker":
            entry["inspect_log"] = _inspect_log_view(Path(summary["log"]), files)
        report[case.id] = entry
    if (out / "cc-local" / "proxy").is_dir():
        report["secrets-demo"] = secrets_demo(out)
    (out / "analysis.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _inspect_log_view(log_path: Path, files: dict[str, str]) -> dict:
    """What Inspect's log holds of each model call the bridge answered: its input, its tools, and the request Inspect sent."""
    from inspect_ai.log import read_eval_log

    sample = read_eval_log(str(log_path), resolve_attachments=True).samples[0]  # long strings are stored as attachments
    calls = [e for e in sample.events if e.event == "model"]
    views = []
    for event in calls:
        request = (event.call.request if event.call else None) or {}
        text = _text_of(request)
        views.append({
            "input_messages": len(event.input),
            "tools": len(event.tools),
            "call_request_keys": sorted(request),
            "call_request_bytes": len(json.dumps(request)),
            "thinking_in_call_request": request.get("thinking"),
            "extra_header_names": sorted(request.get("extra_headers") or {}),
            "system_in_call_request": "system" in request,
            "rule_marker_in_call_request": "CAPTURE-RULE-7319" in text,
            "rule_marker_in_input": any("CAPTURE-RULE-7319" in (m.text or "") for m in event.input),
            "sources": {name: found_in(content, text) for name, content in files.items() if name not in BASE_FILES},
        })
    return {"model_calls": len(calls), "calls": views}


def secrets_demo(out: Path) -> dict:
    """No model: where a key could sit in a captured request, and what Whitney's redaction does with it there.

    A fake key, built here so no key-shaped literal is in the source, is put
    in a captured request's tool result (where an agent that ran `env` would
    put the real one). redact_run covers only a run's `events` and `stderr`,
    so a capture stored in a field of its own keeps the key; the same capture
    passed through `redact` as text loses it.
    """
    fake = "sk-ant-" + "spike" * 6 + "FAKE"
    request = json.loads(sorted((out / "cc-local" / "proxy").glob("*.request.json"))[0].read_text())["body"]
    request["messages"].append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_x", "content": f"ANTHROPIC_API_KEY={fake}"}]})
    run = {"events": "", "stderr": "", "requests": [request]}
    kept = kilo_agent.redact_run(run, [fake])
    as_text = kilo_agent.redact(json.dumps(request), [fake])
    return {
        "redact_run_leaves_a_separate_requests_field": fake in json.dumps(kept["requests"]),
        "redact_on_the_request_text_removes_it": fake not in as_text and kilo_agent.REDACTED in as_text,
        "header_values_recorded_by_the_proxy": False,  # _handler stores sorted header names only
    }


# ---- fixtures ----

# The fixture each case gives: the first model request its proxy recorded. Kilo's plugin captures stay out:
# its chat.params hook was handed the provider's key or login, so only the proxy's record is safe to keep.
FIXTURE_FOR = {"kilo-gateway": "kilo-gateway", "kilo-key": "kilo-key", "cc-local": "claude-code"}
USER_ID_PLACEHOLDER = "USER-ID-PLACEHOLDER"
KILO_EXAMPLE_PATH = "/Users/name/"


def _placeholders(value):
    """`value` with Claude Code's metadata.user_id (an account-derived id) replaced."""
    if isinstance(value, dict):
        return {k: USER_ID_PLACEHOLDER if k == "user_id" else _placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_placeholders(v) for v in value]
    return value


def write_fixtures(out: Path, fixtures: Path) -> list[Path]:
    """One sanitised capture per route: the first model request each harness sent, with a meta file beside it."""
    written = []
    for case_id, name in FIXTURE_FOR.items():
        folder = out / case_id
        summary = json.loads((folder / "summary.json").read_text())
        summary.setdefault("home_dir", None)
        clean = claude_code_shapes.sanitise_claude if summary["target"] == "claude-code" else sanitise
        first = next(json.loads(p.read_text()) for p in sorted((folder / "proxy").glob("*.request.json"))
                     if json.loads(p.read_text())["method"] == "POST")
        body, header_names, path = first["body"], first["header_names"], first["path"]
        text = clean(json.dumps(_placeholders(sanitise_emails(body)), indent=1), summary)
        text = kilo_agent.redact(text, [os.environ.get("ANTHROPIC_API_KEY", ""), *kilo_agent.login_secrets()])
        meta = {
            "case": name, "from": f"spikes/prompt_capture.py, case {case_id}", "via": "the logging proxy", "about": summary["about"],
            "date": summary["date"], "harness_version": summary.get("kilo_version") or summary.get("claude_code_version"),
            "model": summary["model"], "prompt": summary["prompt"], "argv": summary["argv"],
            "project_files": summary["project_files"], "request_path": path,
            "header_names": header_names, "header_values": "never recorded",
            "placeholders": {"as tests/data/kilo-events and claude-code-events": "paths, user name, emails",
                             "metadata.user_id": USER_ID_PLACEHOLDER, "any key or login value": "[redacted]"},
        }
        meta_text = clean(json.dumps(meta, indent=2), summary) + "\n"
        # Kilo's bash tool description quotes `mkdir "/Users/name/My Documents"` as an example; it is no one's path.
        check_nothing_left(text.replace(KILO_EXAMPLE_PATH, ""), f"{name}.request.json")
        check_nothing_left(meta_text, f"{name}.meta.json")
        fixtures.mkdir(parents=True, exist_ok=True)
        for file_name, content in ((f"{name}.request.json", text + "\n"), (f"{name}.meta.json", meta_text)):
            (fixtures / file_name).write_text(content, encoding="utf-8")
            written.append(fixtures / file_name)
    return written


def main(argv: list[str] | None = None) -> int:
    """Run the chosen cases, analyse saved ones, or write the fixtures."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="folder for raw output, outside the repo")
    parser.add_argument("--case", action="append", help="run this case (repeatable); each runs once")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    parser.add_argument("--analyse", action="store_true", help="read saved cases and write analysis.json; runs nothing")
    parser.add_argument("--fixtures", type=Path, help="write the sanitised fixtures here from saved cases; runs nothing")
    args = parser.parse_args(argv)
    if args.list:
        for case in CASES:
            print(f"{case.id:26} {case.target:19} {case.about}")
        return 0
    if not args.out:
        parser.error("--out is needed")
    if args.out.resolve().is_relative_to(REPO):
        parser.error(f"--out must be outside the repo ({REPO}), so raw output is never committed")
    for case in [c for c in CASES if c.id in (args.case or [])]:
        summary = run_case(case, args.out)
        print(f"{case.id}: exit {summary.get('exit_code')}, saved in {args.out / case.id}")
    if args.analyse:
        analyse(args.out)
        print(f"wrote {args.out / 'analysis.json'}")
    if args.fixtures:
        for path in write_fixtures(args.out, args.fixtures):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
