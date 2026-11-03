import json
import os
import threading
import time

import pytest
from inspect_ai.model import get_model

from whitney.credentials import DEFAULT_GATEWAY_URL

pytest.importorskip("openai", reason="install the optional gateway extra to test the Inspect Gateway adapter")


@pytest.fixture
def provider_args(monkeypatch):
    """What KiloGatewayAPI hands Inspect's OpenAI-compatible provider, without building a client."""
    from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI

    from whitney import gateway
    from whitney.credentials import GatewayCredentials

    received: dict = {}
    monkeypatch.setattr(OpenAICompatibleAPI, "__init__", lambda self, **kwargs: received.update(kwargs))
    monkeypatch.setattr(gateway, "resolve_gateway_credentials", lambda: GatewayCredentials("creds-key", "https://creds.example", "t"))
    return received


def test_missing_key_and_url_come_from_the_kilo_credentials(provider_args):
    from inspect_ai.model import GenerateConfig

    from whitney.gateway import KiloGatewayAPI

    config = GenerateConfig(temperature=0)
    KiloGatewayAPI("anthropic/claude-haiku-4.5", config=config, emulate_tools=True)

    assert provider_args == {
        "model_name": "anthropic/claude-haiku-4.5",
        "base_url": "https://creds.example",
        "api_key": "creds-key",
        "config": config,
        "service": "kilo",
        "emulate_tools": True,
    }


def test_a_key_or_url_given_is_kept_and_only_the_other_is_looked_up(provider_args):
    from whitney.gateway import KiloGatewayAPI

    KiloGatewayAPI("m", api_key="given-key")
    assert (provider_args["api_key"], provider_args["base_url"]) == ("given-key", "https://creds.example")
    KiloGatewayAPI("m", base_url="https://given.example")
    assert (provider_args["api_key"], provider_args["base_url"]) == ("creds-key", "https://given.example")


def test_with_both_given_no_credentials_are_looked_up(provider_args, monkeypatch):
    from whitney import gateway

    def unexpected():
        raise AssertionError("credentials looked up although both were given")

    monkeypatch.setattr(gateway, "resolve_gateway_credentials", unexpected)
    gateway.KiloGatewayAPI("m", api_key="k", base_url="https://u.example")
    assert (provider_args["api_key"], provider_args["base_url"]) == ("k", "https://u.example")


@pytest.fixture
def no_kilo_env(monkeypatch, tmp_path):
    """No KILO_* variables, and Kilo's data folder pointed at an empty temp dir."""
    monkeypatch.delenv("KILO_API_KEY", raising=False)
    monkeypatch.delenv("KILO_BASE_URL", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


def test_kilo_provider_uses_kilo_api_key(no_kilo_env, monkeypatch):
    monkeypatch.setenv("KILO_API_KEY", "from-env")
    model = get_model("kilo/anthropic/claude-haiku-4.5", memoize=False)
    assert model.api.api_key == "from-env"
    assert model.api.base_url == DEFAULT_GATEWAY_URL
    assert model.api.model_name == "anthropic/claude-haiku-4.5"


def test_kilo_provider_falls_back_to_kilo_login(no_kilo_env):
    auth = no_kilo_env / "kilo" / "auth.json"
    auth.parent.mkdir()
    auth.write_text(json.dumps({"kilo": {"type": "oauth", "access": "from-login", "expires": (time.time() + 3600) * 1000}}))
    model = get_model("kilo/anthropic/claude-haiku-4.5", memoize=False)
    assert model.api.api_key == "from-login"


def test_kilo_provider_without_credentials_explains_options(no_kilo_env):
    with pytest.raises(Exception, match="kilo auth login"):
        get_model("kilo/anthropic/claude-haiku-4.5", memoize=False)


@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")
def test_kilo_provider_can_be_built_in_a_child_forked_from_a_threaded_process(no_kilo_env, monkeypatch):
    """mutmut runs each mutant's tests in a child forked from its multi-threaded main process.

    The main process has run the same tests once already, so the parent here
    builds the provider before forking too. On macOS the child used to crash
    while building the client, and mutmut recorded a segfault, which the
    mutation score leaves out, instead of whether the tests caught the mutant.
    """
    monkeypatch.setenv("KILO_API_KEY", "from-env")
    get_model("kilo/anthropic/claude-haiku-4.5", memoize=False)
    stop = threading.Event()
    other_thread = threading.Thread(target=stop.wait)
    other_thread.start()
    try:
        pid = os.fork()
        if pid == 0:
            try:
                get_model("kilo/anthropic/claude-haiku-4.5", memoize=False)
                code = 0
            except BaseException:
                code = 1
            os._exit(code)
        _, status = os.waitpid(pid, 0)
    finally:
        stop.set()
        other_thread.join()
    assert os.waitstatus_to_exitcode(status) == 0
