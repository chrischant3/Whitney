import json

import pytest

from whitney.credentials import (
    DEFAULT_GATEWAY_URL,
    CredentialsError,
    apply_to_environ,
    kilo_auth_path,
    resolve_gateway_credentials,
)

NOW = 1_800_000_000.0  # seconds


def auth_file(tmp_path, entry):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"kilo": entry}))
    return path


def test_kilo_api_key_from_environment_wins(tmp_path):
    path = auth_file(tmp_path, {"type": "oauth", "access": "from-login", "expires": (NOW + 3600) * 1000})
    creds = resolve_gateway_credentials({"KILO_API_KEY": "from-env"}, path, now=NOW)
    assert creds.api_key == "from-env"
    assert creds.base_url == DEFAULT_GATEWAY_URL
    assert "KILO_API_KEY" in creds.source


def test_base_url_can_be_overridden(tmp_path):
    creds = resolve_gateway_credentials(
        {"KILO_API_KEY": "k", "KILO_BASE_URL": "https://gw.example/api"}, tmp_path / "none.json", now=NOW
    )
    assert creds.base_url == "https://gw.example/api"


def test_falls_back_to_kilo_login(tmp_path):
    path = auth_file(tmp_path, {"type": "oauth", "access": "from-login", "expires": (NOW + 3600) * 1000})
    creds = resolve_gateway_credentials({}, path, now=NOW)
    assert creds.api_key == "from-login"
    assert "kilo login" in creds.source


def test_expiry_in_seconds_is_understood(tmp_path):
    path = auth_file(tmp_path, {"type": "oauth", "access": "t", "expires": NOW + 3600})
    assert resolve_gateway_credentials({}, path, now=NOW).api_key == "t"


def test_expired_login_says_how_to_refresh(tmp_path):
    path = auth_file(tmp_path, {"type": "oauth", "access": "t", "expires": (NOW - 60) * 1000})
    with pytest.raises(CredentialsError, match="kilo auth login"):
        resolve_gateway_credentials({}, path, now=NOW)


def test_api_key_style_login_entry(tmp_path):
    path = auth_file(tmp_path, {"type": "api", "key": "kilo-key"})
    assert resolve_gateway_credentials({}, path, now=NOW).api_key == "kilo-key"


def test_nothing_configured_explains_both_options(tmp_path):
    with pytest.raises(CredentialsError) as e:
        resolve_gateway_credentials({}, tmp_path / "missing.json", now=NOW)
    assert "KILO_API_KEY" in str(e.value) and "kilo auth login" in str(e.value)


def test_login_without_kilo_entry_is_not_used(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"anthropic": {"type": "api", "key": "x"}}))
    with pytest.raises(CredentialsError):
        resolve_gateway_credentials({}, path, now=NOW)


def test_apply_sets_missing_variables_only(tmp_path):
    creds = resolve_gateway_credentials({"KILO_API_KEY": "k"}, tmp_path / "none.json", now=NOW)
    env = {"KILO_BASE_URL": "https://keep.example"}
    apply_to_environ(creds, env)
    assert env == {"KILO_BASE_URL": "https://keep.example", "KILO_API_KEY": "k"}


def test_auth_path_follows_xdg_data_home(tmp_path):
    assert kilo_auth_path({"XDG_DATA_HOME": str(tmp_path)}) == tmp_path / "kilo" / "auth.json"
    assert str(kilo_auth_path({"HOME": "/home/x"})) == "/home/x/.local/share/kilo/auth.json"


@pytest.mark.parametrize("base_url", ["http://gw.example/api", "ftp://gw.example", "gw.example/api"])
def test_a_base_url_without_https_is_refused(tmp_path, base_url):
    # The key is sent to it as a bearer token; over plain http, anyone on the path can read it.
    with pytest.raises(CredentialsError, match="https"):
        resolve_gateway_credentials({"KILO_API_KEY": "k", "KILO_BASE_URL": base_url}, tmp_path / "none.json", now=NOW)


@pytest.mark.parametrize("base_url", ["http://localhost:8080/v1", "http://127.0.0.1:4000", "http://[::1]:9000"])
def test_plain_http_is_allowed_to_a_local_proxy(tmp_path, base_url):
    creds = resolve_gateway_credentials({"KILO_API_KEY": "k", "KILO_BASE_URL": base_url}, tmp_path / "none.json", now=NOW)
    assert creds.base_url == base_url


def test_the_kilo_login_is_only_sent_to_kilo(tmp_path):
    path = auth_file(tmp_path, {"type": "oauth", "access": "from-login", "expires": (NOW + 3600) * 1000})
    with pytest.raises(CredentialsError, match="KILO_API_KEY"):
        resolve_gateway_credentials({"KILO_BASE_URL": "https://gw.example/api"}, path, now=NOW)

    creds = resolve_gateway_credentials({"KILO_BASE_URL": "https://eu.api.kilo.ai/api/gateway"}, path, now=NOW)
    assert creds.api_key == "from-login"
