import json
import time

import pytest
from inspect_ai.model import get_model

from whitney.credentials import DEFAULT_GATEWAY_URL

pytest.importorskip("openai", reason="install the optional gateway extra to test the Inspect Gateway adapter")


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
