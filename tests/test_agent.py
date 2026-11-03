import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from inspect_ai.agent import AgentState
from inspect_ai.model import ChatMessageUser

from whitney.agent import _append_final_message, _run_kilo, _run_local


def test_local_run_sets_pwd_to_sandbox_project():
    class Sandbox:
        calls = []

        async def exec(self, command, **kwargs):
            self.calls.append((command, kwargs))
            if command == ["pwd"]:
                return SimpleNamespace(stdout="/tmp/eval/project\n")
            return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    state = SimpleNamespace(messages=[SimpleNamespace(text="test prompt")])
    sbox = Sandbox()

    run = asyncio.run(
        _run_kilo(sbox, state, "my-proxy/model-a", False, {"KILO_CONFIG": "/tmp/config.json"}, 10)
    )

    assert sbox.calls[1][1]["env"] == {"KILO_CONFIG": "/tmp/config.json", "PWD": "/tmp/eval/project"}
    assert run["exit_code"] == 0


def test_kilo_final_text_becomes_inspect_completion():
    state = AgentState(messages=[ChatMessageUser(content="generate code")])
    events = '{"type":"text","part":{"text":"```python\\nprint(1)\\n```"}}\n'

    _append_final_message(state, {"events": events}, "my-proxy/model-a")

    assert state.output.completion == "```python\nprint(1)\n```"


def test_local_agent_materializes_config_files_and_sets_pwd(monkeypatch):
    captured = {}

    class Sandbox:
        writes = []

        async def write_file(self, path, content):
            self.writes.append((path, content))

    class Process:
        returncode = 0

        async def communicate(self):
            return b"event", b""

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured.update(command=command, **kwargs)
        project = kwargs["cwd"]
        captured["agents"] = (project / "AGENTS.md").read_text()
        (project / "solution.py").write_text("def answer(): return 42\n")
        return Process()

    monkeypatch.setattr("whitney.agent.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    state = SimpleNamespace(messages=[SimpleNamespace(text="test prompt")])
    sbox = Sandbox()

    run = asyncio.run(
        _run_local(
            sbox,
            state,
            "my-proxy/model-a",
            False,
            {"KILO_CONFIG": "/tmp/config.json"},
            10,
            {"AGENTS.md": "test instructions"},
            {"solution.py": "/workspace/solution.py"},
        )
    )

    assert captured["cwd"].name == "project"
    assert captured["env"]["PWD"] == str(captured["cwd"])
    assert captured["env"]["KILO_CONFIG"] == "/tmp/config.json"
    assert captured["agents"] == "test instructions"
    assert sbox.writes == [("/workspace/solution.py", "def answer(): return 42\n")]
    assert run["exit_code"] == 0


def test_without_a_provider_key_the_overlay_only_adds_permissions():
    from whitney.agent import local_config_overlay

    overlay = local_config_overlay("my-proxy/model-a", {"bash": "allow"}, environ={})

    assert overlay == {"permission": {"bash": "allow"}}


def test_without_permissions_or_provider_key_there_is_no_overlay():
    from whitney.agent import local_config_overlay

    assert local_config_overlay("my-proxy/model-a", None, environ={}) is None


def test_provider_key_overlay_references_the_env_var_not_its_value():
    from whitney.agent import local_config_overlay

    overlay = local_config_overlay(
        "anthropic/claude-haiku-4-5", None, api_key_env="ANTHROPIC_API_KEY", environ={"ANTHROPIC_API_KEY": "sk-secret"}
    )

    assert overlay == {"provider": {"anthropic": {"options": {"apiKey": "{env:ANTHROPIC_API_KEY}"}}}}
    assert "sk-secret" not in json.dumps(overlay)


def test_provider_overlay_can_set_a_base_url():
    from whitney.agent import local_config_overlay

    overlay = local_config_overlay(
        "openai/gpt-5",
        {"edit": "allow"},
        api_key_env="MY_KEY",
        base_url="https://proxy.example/v1",
        environ={"MY_KEY": "x"},
    )

    assert overlay == {
        "permission": {"edit": "allow"},
        "provider": {"openai": {"options": {"apiKey": "{env:MY_KEY}", "baseURL": "https://proxy.example/v1"}}},
    }


def test_missing_api_key_env_var_is_reported_by_name():
    import pytest

    from whitney.agent import local_config_overlay

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        local_config_overlay("anthropic/claude-haiku-4-5", None, api_key_env="ANTHROPIC_API_KEY", environ={})


def test_docker_mode_rejects_a_provider_key():
    import pytest

    from whitney.agent import kilo

    with pytest.raises(ValueError, match="--model"):
        kilo(model="anthropic/claude-haiku-4-5", execution="docker", api_key_env="ANTHROPIC_API_KEY")


def test_local_agent_gives_kilo_the_provider_key_overlay(monkeypatch):
    from whitney.agent import kilo

    seen = {}

    class Process:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*command, **kwargs):
        seen["command"] = command
        seen["overlay"] = json.loads(Path(kwargs["env"]["KILO_CONFIG"]).read_text())
        seen["key_in_env"] = kwargs["env"].get("ANTHROPIC_API_KEY")
        return Process()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setattr("whitney.agent.asyncio.create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr("whitney.agent.sandbox", lambda: SimpleNamespace())
    monkeypatch.setattr("whitney.agent.store", lambda: SimpleNamespace(set=lambda *a: None))
    agent = kilo(
        model="anthropic/claude-haiku-4-5",
        harness_permissions={"bash": "allow"},
        local_project_files={"AGENTS.md": "rules"},
        api_key_env="ANTHROPIC_API_KEY",
    )

    asyncio.run(agent(AgentState(messages=[ChatMessageUser(content="hi")])))

    assert seen["command"][:7] == ("kilo", "run", "--pure", "--format", "json", "-m", "anthropic/claude-haiku-4-5")
    assert seen["overlay"]["provider"] == {"anthropic": {"options": {"apiKey": "{env:ANTHROPIC_API_KEY}"}}}
    assert seen["overlay"]["permission"] == {"bash": "allow"}
    assert seen["key_in_env"] == "sk-secret"


def test_the_agent_has_no_default_model():
    import pytest

    from whitney.agent import kilo

    with pytest.raises(TypeError, match="'model'"):
        kilo()


@pytest.mark.parametrize(
    ("model", "kilo_cli_model"),
    [
        ("kilo/anthropic/claude-haiku-4.5", "anthropic/claude-haiku-4-5"),
        ("kilo/openai/gpt-5.1", "openai/gpt-5.1"),
        ("anthropic/claude-haiku-4-5", "anthropic/claude-haiku-4-5"),
        ("openai/gpt-5", "openai/gpt-5"),
    ],
)
def test_docker_mode_derives_the_model_kilo_is_told_to_use(model, kilo_cli_model):
    from whitney.agent import bridge_kilo_model

    assert bridge_kilo_model(model) == kilo_cli_model


@pytest.mark.parametrize("model", ["kilo/google/gemini-3-pro", "kilo/~anthropic/claude-haiku-latest", "google/gemini-3", "kilo"])
def test_docker_mode_refuses_models_whose_format_the_bridge_cannot_serve(model):
    from whitney.agent import bridge_kilo_model

    with pytest.raises(ValueError, match="^Docker mode needs an Anthropic or OpenAI model, e.g. kilo/anthropic/claude-haiku-4.5 or "):
        bridge_kilo_model(model)


@pytest.mark.parametrize("vendor, package", [("anthropic", "anthropic"), ("openai", "openai")])
def test_the_gateway_extra_installs_what_the_bridge_needs_for_each_docker_model_family(vendor, package):
    # In Docker mode Kilo speaks this vendor's API to Inspect's bridge, which imports
    # the vendor's SDK to serve it; without the package the run fails mid-sample.
    import re
    import tomllib

    from whitney.agent import BRIDGE_PROVIDERS

    assert vendor in BRIDGE_PROVIDERS
    pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    gateway = pyproject["project"]["optional-dependencies"]["gateway"]
    assert package in {re.split(r"[\[<>=!~ ;]", requirement, maxsplit=1)[0] for requirement in gateway}


def test_docker_agent_refuses_an_unsupported_model_before_any_run():
    from whitney.agent import kilo

    with pytest.raises(ValueError, match="^Docker mode needs an Anthropic or OpenAI model, e.g. kilo/anthropic/claude-haiku-4.5 or "):
        kilo(model="kilo/google/gemini-3-pro", execution="docker")


def test_docker_run_tells_kilo_the_derived_model_and_points_its_provider_at_the_bridge(monkeypatch):
    from contextlib import asynccontextmanager

    from whitney import agent as agent_module
    from whitney.agent import kilo

    written, ran = {}, {}

    class Sandbox:
        async def exec(self, command, **kwargs):
            if command[0] == "kilo":
                ran["command"], ran["env"] = command, kwargs.get("env", {})
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            return SimpleNamespace(returncode=0, stdout="/root/project\n", stderr="")

        async def write_file(self, path, content):
            written[path] = content

    bridge_state = AgentState(messages=[])

    @asynccontextmanager
    async def fake_bridge(state, model):
        ran["bridge_model"] = model
        yield SimpleNamespace(port=13131, state=bridge_state)

    monkeypatch.setattr(agent_module, "sandbox_agent_bridge", fake_bridge)
    monkeypatch.setattr(agent_module, "sandbox", lambda: Sandbox())
    monkeypatch.setattr(agent_module, "store", lambda: SimpleNamespace(set=lambda *a: None))

    state = asyncio.run(
        kilo(model="kilo/anthropic/claude-haiku-4.5", execution="docker")(AgentState(messages=[ChatMessageUser(content="hi")]))
    )

    config = json.loads(written[f"{agent_module.DOCKER_HOME}/.config/kilo/kilo.json"])
    assert ran["env"]["HOME"] == agent_module.DOCKER_HOME == "/home/node"  # the image's unprivileged user
    assert config["provider"] == {"anthropic": {"options": {"baseURL": "http://localhost:13131/v1", "apiKey": "sk-none"}}}
    assert ran["command"][ran["command"].index("-m") + 1] == "anthropic/claude-haiku-4-5"
    assert ran["bridge_model"] == "inspect"  # every request is answered with the eval's --model
    assert state is bridge_state  # in Docker the bridge records the model's replies itself


def test_the_docker_sandbox_runs_kilo_unprivileged_and_offline():
    # Kilo reaches the model only through Inspect's bridge, inside the container. As root, with
    # a network, an agent could replace the git the scorer trusts or reach host services.
    import re

    import yaml

    docker = Path(__file__).parents[1] / "docker"
    service = yaml.safe_load((docker / "compose.yaml").read_text())["services"]["default"]
    assert service["network_mode"] == "none"
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    dockerfile = (docker / "Dockerfile").read_text()
    assert re.search(r"^FROM \S+@sha256:[0-9a-f]{64}$", dockerfile, re.M)  # the base image is pinned by digest
    assert re.findall(r"^USER (\S+)$", dockerfile, re.M)[-1] == "node"
