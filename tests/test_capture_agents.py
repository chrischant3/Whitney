"""Capture and thinking through the Kilo and Claude Code agents (Phase 5 Task 31).

A fake `kilo` or `claude` on PATH, written in Python, sends model requests to
the address it was given (Kilo's `baseURL` in its KILO_CONFIG overlay, Claude
Code's ANTHROPIC_BASE_URL) and prints an event stream in the shapes the real
harnesses printed (tests/data/kilo-events, tests/data/claude-code-events).
Its requests reach a fake upstream through Whitney's real proxy, so these
tests see what was forwarded, what was stored, and what never was.
"""

import asyncio
import json
import socket
import sys
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser
from test_capture_proxy import Upstream

from whitney import agent as agent_module
from whitney import capture_proxy
from whitney import claude_code as claude_module
from whitney.agent import RUN_STORE_KEY, kilo
from whitney.capture import LEVEL_NOT_RECORDED, NOT_SET
from whitney.claude_code import claude_code

KEY = "sk-ant-fake-capture-key-0123456789"
LOGIN = "kilo-oauth-fake-access-0123456789"
AGENTS_MD = "# Project rules\n\n- Never commit .env files or secrets.\n- Rule marker: CAPTURE-RULE-7319\n"
TODAY_CLAUDE_FIELDS = {"exit_code", "events", "stderr", "ended_early", "permission_denials", "api_key_source", "final_text"}
TODAY_KILO_FIELDS = {"exit_code", "events", "stderr"}

FAKE = r"""#!PYTHON
import http.client, json, os, sys, time, urllib.parse

here = os.path.dirname(os.path.abspath(__file__))
control = json.load(open(os.path.join(here, "control.json")))
args = sys.argv[1:]
json.dump(args, open("args.json", "w"))
overlay = json.load(open(os.environ["KILO_CONFIG"])) if os.environ.get("KILO_CONFIG") else None
json.dump({"overlay": overlay, "base": os.environ.get("ANTHROPIC_BASE_URL")}, open("seen.json", "w"))


def send(base, method, path, body, headers):
    address = urllib.parse.urlsplit(base)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    connection.request(method, address.path + path, body=data, headers=headers)
    connection.getresponse().read()


key = os.environ.get("ANTHROPIC_API_KEY", "")
if os.path.basename(sys.argv[0]) == "claude":
    sent_model = args[args.index("--model") + 1]
    base, auth = os.environ.get("ANTHROPIC_BASE_URL"), {"x-api-key": key}
    calls = [("POST", "/v1/messages?beta=true", body) for body in control["requests"]]
else:
    provider, _, sent_model = args[args.index("-m") + 1].partition("/")  # Kilo names the model without its provider
    base = ((((overlay or {}).get("provider") or {}).get(provider) or {}).get("options") or {}).get("baseURL")
    login = os.path.join(os.environ["HOME"], ".local/share/kilo/auth.json")
    token = json.load(open(login))["kilo"]["access"] if os.path.exists(login) else ""
    auth = {"authorization": "Bearer " + token} if provider == "kilo" else {"x-api-key": key}
    prefix = "/api/openrouter" if provider == "kilo" else ""
    calls = [("GET", prefix + "/models", None)] + [("POST", prefix + "/messages", body) for body in control["requests"]]
if base:
    for method, path, body in calls:
        if body is not None:
            body = json.loads(json.dumps(body).replace("KEY-HERE", key or token).replace("MODEL-HERE", sent_model))
        send(base, method, path, body, {**auth, "content-type": "application/json"})
time.sleep(control.get("sleep", 0))
if os.path.basename(sys.argv[0]) == "claude":
    settings = json.loads(args[args.index("--settings") + 1])
    thought = "Add 17 and 25." if settings.get("showThinkingSummaries") else ""
    print(json.dumps({"type": "system", "subtype": "init", "apiKeySource": "ANTHROPIC_API_KEY"}))
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": thought, "signature": "s"}]}}))
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "permission_denials": [], "result": "42"}))
else:
    print('{"type":"step_start"}')
    if "--thinking" in args:
        print(json.dumps({"type": "reasoning", "part": {"text": "17 plus 25 is 42."}}))
    print('{"type":"text","part":{"text":"42"}}')
""".replace("PYTHON", sys.executable)


def first_request(system: str = AGENTS_MD) -> dict:
    """A model request in Anthropic's format holding the key the way an agent's shell could put it there."""
    return {
        "model": "MODEL-HERE",
        "system": [{"type": "text", "text": f"Instructions from: /tmp/project/AGENTS.md\n{system}"}],
        "messages": [
            {"role": "user", "content": "Reply with PONG."},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ANTHROPIC_API_KEY=KEY-HERE\n"}]},
        ],
        "tools": [{"name": "bash"}],
        "thinking": {"budget_tokens": 31999, "type": "enabled", "display": "updates"},
        "leaked": "KEY-HERE",
    }


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """The fake harnesses on PATH, a fake upstream every proxy forwards to, and a fake sandbox and store."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("kilo", "claude"):
        (bin_dir / name).write_text(FAKE)
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    home = tmp_path / "user-home"
    (home / ".local" / "share" / "kilo").mkdir(parents=True)
    (home / ".local" / "share" / "kilo" / "auth.json").write_text(json.dumps({"kilo": {"type": "oauth", "access": LOGIN}}))
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(name, raising=False)
    upstream = Upstream()
    monkeypatch.setattr(capture_proxy, "CLAUDE_CODE_UPSTREAM", upstream.url)
    monkeypatch.setitem(capture_proxy.UPSTREAMS, "kilo", upstream.url)
    monkeypatch.setitem(capture_proxy.UPSTREAMS, "anthropic", upstream.url + "/v1")
    project = tmp_path / "project"
    project.mkdir()
    stored: dict = {}
    sbox = SimpleNamespace(exec=_pwd(project))
    for module in (agent_module, claude_module):
        monkeypatch.setattr(module, "sandbox", lambda: sbox)
        monkeypatch.setattr(module, "store", lambda: SimpleNamespace(set=stored.__setitem__))

    def control(**values):
        (bin_dir / "control.json").write_text(json.dumps({"requests": [first_request()], **values}))

    control()
    yield SimpleNamespace(project=project, stored=stored, upstream=upstream, control=control)
    upstream.server.shutdown()
    upstream.server.server_close()


def _pwd(project: Path):
    async def exec(command, **kwargs):
        return SimpleNamespace(stdout=f"{project}\n", returncode=0, stderr="")

    return exec


def run(agent, prompt="Reply with PONG."):
    return asyncio.run(agent(AgentState(messages=[ChatMessageUser(content=prompt)])))


def seen(harness) -> dict:
    return json.loads((harness.project / "seen.json").read_text())


def args(harness) -> list[str]:
    return json.loads((harness.project / "args.json").read_text())


def refused(origin: str) -> bool:
    address = urllib.parse.urlsplit(origin)
    try:
        socket.create_connection((address.hostname, address.port), timeout=5).close()
    except ConnectionRefusedError:
        return True
    return False


# --- a run with neither -----------------------------------------------------------------------------


def test_a_claude_code_run_without_capture_or_thinking_starts_no_proxy_and_records_today_s_fields(harness):
    run(claude_code(model="anthropic/claude-haiku-4-5", harness_permissions={"allow": ["Bash"]}))

    assert set(harness.stored[RUN_STORE_KEY]) == TODAY_CLAUDE_FIELDS
    assert seen(harness)["base"] is None
    assert json.loads(args(harness)[args(harness).index("--settings") + 1]) == {"permissions": {"allow": ["Bash"]}}
    assert harness.upstream.seen == []


def test_a_kilo_run_without_capture_or_thinking_starts_no_proxy_and_records_today_s_fields(harness):
    run(kilo(model="kilo/anthropic/claude-haiku-4.5", harness_permissions={"bash": "allow"}))

    assert set(harness.stored[RUN_STORE_KEY]) == TODAY_KILO_FIELDS
    assert seen(harness)["overlay"] == {"permission": {"bash": "allow"}}
    assert args(harness) == ["run", "--pure", "--format", "json", "-m", "kilo/anthropic/claude-haiku-4.5", "--", "Reply with PONG."]
    assert harness.upstream.seen == []


# --- claude-code, local -----------------------------------------------------------------------------


def test_claude_code_with_capture_keeps_its_first_request_and_finds_the_rules_in_it(harness):
    run(claude_code(model="anthropic/claude-haiku-4-5", capture_prompt=True, capture_texts={"AGENTS.md": AGENTS_MD}))

    run_record = harness.stored[RUN_STORE_KEY]
    sent = run_record["sent"]
    assert urllib.parse.urlsplit(seen(harness)["base"]).hostname == "127.0.0.1"
    assert harness.upstream.seen[0]["path"] == "/v1/messages?beta=true"
    assert sent["via"] == "proxy" and sent["first_request"] == 0
    assert sent["found"] == {"AGENTS.md": {"in": "first", "field": "system prompt", "heading": "Instructions from: …/AGENTS.md"}}
    assert sent["first_body"]["system"] == first_request()["system"]
    assert sent["requests"][0]["path"] == "/v1/messages" and sent["requests"][0]["query_removed"] is True
    assert run_record["thinking"] == {
        "set": NOT_SET, "sent": '"thinking": {"budget_tokens": 31999, "type": "enabled", "display": "updates"}',
        "text": "withheld: the thinking blocks are empty",
    }
    assert set(run_record) == TODAY_CLAUDE_FIELDS | {"sent", "thinking"}


def test_claude_code_s_key_in_headers_and_body_never_reaches_the_stored_record(harness):
    """Task 31 Evidence item 2: the key in a header, in a separate JSON field and in a tool result."""
    run(claude_code(model="anthropic/claude-haiku-4-5", capture_prompt=True))

    stored = json.dumps(harness.stored[RUN_STORE_KEY])
    assert KEY not in stored
    assert harness.stored[RUN_STORE_KEY]["sent"]["first_body"]["leaked"] == "[redacted]"
    assert harness.upstream.headers()["x-api-key"] == [KEY]  # it was forwarded, as the harness sent it
    assert KEY.encode() in harness.upstream.seen[0]["body"]


def test_claude_code_s_proxy_is_shut_down_when_its_run_times_out(harness):
    harness.control(sleep=10)

    run(claude_code(model="anthropic/claude-haiku-4-5", capture_prompt=True, timeout=2))

    assert harness.stored[RUN_STORE_KEY]["exit_code"] == 124
    assert refused(seen(harness)["base"])


def test_claude_code_with_thinking_is_told_to_show_it_and_states_what_it_was_told(harness):
    run(claude_code(model="anthropic/claude-haiku-4-5", harness_permissions={"allow": ["Bash"]}, thinking=True))

    settings = json.loads(args(harness)[args(harness).index("--settings") + 1])
    assert settings == {"permissions": {"allow": ["Bash"]}, "showThinkingSummaries": True}
    assert harness.stored[RUN_STORE_KEY]["thinking"] == {
        "set": '--settings {"showThinkingSummaries": true}', "sent": LEVEL_NOT_RECORDED, "text": "in the events",
    }
    assert "sent" not in harness.stored[RUN_STORE_KEY]
    assert seen(harness)["base"] is None  # thinking alone starts no proxy


# --- kilo, local ---------------------------------------------------------------------------------------


def test_kilo_on_the_gateway_is_given_the_proxy_s_origin_alone_and_its_paths_reach_the_gateway_unchanged(harness):
    run(kilo(model="kilo/anthropic/claude-haiku-4.5", capture_prompt=True, capture_texts={"AGENTS.md": AGENTS_MD}))

    base = seen(harness)["overlay"]["provider"]["kilo"]["options"]["baseURL"]
    assert urllib.parse.urlsplit(base).path == "" and urllib.parse.urlsplit(base).hostname == "127.0.0.1"
    assert [(s["method"], s["path"]) for s in harness.upstream.seen] == [("GET", "/api/openrouter/models"), ("POST", "/api/openrouter/messages")]
    assert harness.upstream.headers(1)["authorization"] == [f"Bearer {LOGIN}"]
    run_record = harness.stored[RUN_STORE_KEY]
    assert run_record["sent"]["first_request"] == 1 and run_record["sent"]["found"]["AGENTS.md"]["in"] == "first"
    assert run_record["sent"]["first_body"]["model"] == "anthropic/claude-haiku-4.5"
    assert [r["model_request"] for r in run_record["sent"]["requests"]] == [False, True]
    assert run_record["thinking"]["set"] == NOT_SET
    assert LOGIN not in json.dumps(run_record)  # the login's token, which the fake put in its request, is redacted
    assert refused(base)


def test_kilo_on_a_provider_key_reaches_that_provider_s_api_through_the_proxy(harness):
    run(kilo(model="anthropic/claude-haiku-4-5", api_key_env="ANTHROPIC_API_KEY", capture_prompt=True))

    base = seen(harness)["overlay"]["provider"]["anthropic"]["options"]["baseURL"]
    assert urllib.parse.urlsplit(base).path == ""
    assert seen(harness)["overlay"]["provider"]["anthropic"]["options"]["apiKey"] == "{env:ANTHROPIC_API_KEY}"
    assert [s["path"] for s in harness.upstream.seen] == ["/v1/models", "/v1/messages"]
    assert KEY not in json.dumps(harness.stored[RUN_STORE_KEY])


def test_kilo_keeps_the_base_url_its_global_config_sets_as_the_upstream(harness, tmp_path):
    config = Path(tmp_path / "user-home" / ".config" / "kilo")
    config.mkdir(parents=True)
    (config / "kilo.json").write_text(json.dumps({"provider": {"kilo": {"options": {"baseURL": harness.upstream.url + "/own"}}}}))

    run(kilo(model="kilo/anthropic/claude-haiku-4.5", capture_prompt=True))

    assert harness.upstream.seen[0]["path"] == "/own/api/openrouter/models"


def test_kilo_with_thinking_runs_the_high_variant_and_states_it(harness):
    run(kilo(model="kilo/anthropic/claude-haiku-4.5", thinking=True, agent_name="reviewer"))

    assert args(harness) == [
        "run", "--pure", "--format", "json", "-m", "kilo/anthropic/claude-haiku-4.5", "--agent", "reviewer",
        "--variant", "high", "--thinking", "--", "Reply with PONG.",
    ]  # fmt: skip
    assert harness.stored[RUN_STORE_KEY]["thinking"] == {"set": "kilo run --variant high --thinking", "sent": LEVEL_NOT_RECORDED, "text": "in the events"}


def test_kilo_with_capture_refuses_a_base_url_of_its_own():
    with pytest.raises(ValueError, match="base_url and capture_prompt"):
        kilo(model="kilo/anthropic/claude-haiku-4.5", capture_prompt=True, base_url="http://127.0.0.1:1")


def test_kilo_with_capture_refuses_a_provider_whose_address_it_does_not_know(harness):
    with pytest.raises(ValueError, match="knows the API address of kilo/, anthropic/, openai/ models only"):
        kilo(model="openrouter/anthropic/claude-haiku-4.5", capture_prompt=True)


# --- Docker: Inspect's log ------------------------------------------------------------------------------


class FakeTranscript:
    """Stands in for Inspect's transcript: the model events the bridge added while the harness ran."""

    def __init__(self, events, attachments=None):
        self.events = events
        self.attachments = attachments or {}


def model_event(request):
    return SimpleNamespace(event="model", call=SimpleNamespace(request=request))


@pytest.fixture
def container(monkeypatch):
    """A fake sandbox and bridge for both agents; the bridge adds `bridged` model events when the harness runs."""
    stored: dict = {}
    transcript = FakeTranscript([model_event({"messages": [], "note": "before this run"})])
    bridged: list = []
    ran: dict = {}

    class Sandbox:
        async def exec(self, command, **kwargs):
            if command[0] in ("kilo", "claude") and "--version" not in command:
                ran["command"] = command
                if bridged:  # the bridge's model events, added while the harness ran
                    transcript.events = [*transcript.events, *bridged]
                return SimpleNamespace(returncode=0, stdout='{"type":"text","part":{"text":"42"}}\n', stderr="", success=True)
            return SimpleNamespace(returncode=0, stdout="/root/project\n", stderr="", success=True)

        async def write_file(self, path, content):
            pass

    @asynccontextmanager
    async def bridge(state, model):
        yield SimpleNamespace(port=13131, state=state)

    for module in (agent_module, claude_module):
        monkeypatch.setattr(module, "sandbox_agent_bridge", bridge)
        monkeypatch.setattr(module, "sandbox", lambda: Sandbox())
        monkeypatch.setattr(module, "store", lambda: SimpleNamespace(set=stored.__setitem__))
        monkeypatch.setattr(module, "transcript", lambda: transcript)
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    return SimpleNamespace(stored=stored, bridged=bridged, transcript=transcript, ran=ran)


def test_a_docker_run_with_capture_reads_the_bridged_requests_from_inspect_s_log(container):
    container.bridged += [
        model_event({"system": "attachment://rules", "messages": [{"role": "user", "content": f"hi {KEY}"}]}),
        SimpleNamespace(event="model", call=None),
    ]
    container.transcript.attachments = {"rules": AGENTS_MD}

    run(claude_code(model="anthropic/claude-haiku-4-5", execution="docker", api_key_env=None, capture_prompt=True, capture_texts={"AGENTS.md": AGENTS_MD}))

    sent = container.stored[RUN_STORE_KEY]["sent"]
    assert sent["via"] == "inspect-log" and sent["unlogged"] == 1
    assert sent["found"] == {"AGENTS.md": {"in": "first", "field": "system prompt", "heading": None}}
    assert sent["first_body"] == {"system": AGENTS_MD, "messages": [{"role": "user", "content": "hi [redacted]"}]}
    assert container.stored[RUN_STORE_KEY]["thinking"] == {"asked": NOT_SET, "applied": "no thinking setting in the request", "text": "none in the events"}


def test_a_docker_run_with_thinking_states_what_it_asked_for_and_what_reached_the_model(container):
    container.bridged.append(model_event({"messages": [], "thinking": {"type": "enabled", "budget_tokens": 31999}}))

    run(kilo(model="kilo/anthropic/claude-haiku-4.5", execution="docker", thinking=True))

    assert container.ran["command"][-5:-2] == ["--variant", "high", "--thinking"]
    assert container.stored[RUN_STORE_KEY]["thinking"] == {
        "asked": "kilo run --variant high --thinking; Inspect's reasoning_tokens 31999",
        "applied": '"thinking": {"type": "enabled", "budget_tokens": 31999}',
        "text": "none in the events",
    }
    assert "sent" not in container.stored[RUN_STORE_KEY]


def test_a_docker_claude_code_run_with_thinking_never_states_a_level_that_was_not_applied(container):
    container.bridged.append(model_event({"messages": []}))

    run(claude_code(model="anthropic/claude-haiku-4-5", execution="docker", api_key_env=None, thinking=True))

    settings = json.loads(container.ran["command"][container.ran["command"].index("--settings") + 1])
    assert settings["showThinkingSummaries"] is True
    assert container.stored[RUN_STORE_KEY]["thinking"]["asked"] == '--settings {"showThinkingSummaries": true}; Inspect\'s reasoning_tokens 31999'
    assert container.stored[RUN_STORE_KEY]["thinking"]["applied"] == "no thinking setting in the request"


def test_a_docker_run_without_either_reads_nothing_from_inspect_s_log(container):
    container.transcript.events = None  # reading it would fail

    run(claude_code(model="anthropic/claude-haiku-4-5", execution="docker", api_key_env=None))
    run(kilo(model="kilo/anthropic/claude-haiku-4.5", execution="docker"))

    assert "thinking" not in container.stored[RUN_STORE_KEY]


def test_a_docker_kilo_run_with_capture_finds_nothing_of_a_text_it_was_not_sent(container):
    request = {"messages": [{"role": "user", "content": "hi"}]}
    container.bridged.append(model_event(request))

    run(kilo(model="kilo/anthropic/claude-haiku-4.5", execution="docker", capture_prompt=True, capture_texts={"AGENTS.md": AGENTS_MD}))

    assert container.stored[RUN_STORE_KEY]["sent"]["found"] == {"AGENTS.md": {"in": None}}
    assert container.stored[RUN_STORE_KEY]["sent"]["requests"] == [
        {"method": None, "path": None, "bytes": len(json.dumps(request).encode()), "model_request": True}
    ]
