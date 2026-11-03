"""Find credentials for the Kilo Gateway, which serves the model calls in the evals.

Order:
1. `KILO_API_KEY` (and optionally `KILO_BASE_URL`) from the environment or `.env`;
2. otherwise the Kilo login in your global Kilo setup (`kilo auth login`), read
   from Kilo's data folder (`~/.local/share/kilo/auth.json`).

The key stays on the host. Inspect uses it to call the gateway, and Kilo in
the sandbox only ever talks to Inspect's bridge.

    python -m whitney.credentials    # says which source would be used, never prints the key
"""

import json
import os
import sys
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_GATEWAY_URL = "https://api.kilo.ai/api/gateway"
GATEWAY_MODEL_PREFIX = "openai-api/kilo/"
KILO_HOST = "kilo.ai"  # the Kilo login is only sent here or to a subdomain
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}  # plain http is allowed to a local proxy


class CredentialsError(RuntimeError):
    """No usable Kilo Gateway credentials; the message says how to provide them."""


@dataclass(frozen=True)
class GatewayCredentials:
    api_key: str
    base_url: str
    source: str

    def __repr__(self) -> str:  # never show the key in logs or tracebacks
        return f"GatewayCredentials(base_url={self.base_url!r}, source={self.source!r})"


def kilo_auth_path(environ: Mapping[str, str] = os.environ) -> Path:
    """Where Kilo stores logins: $XDG_DATA_HOME/kilo/auth.json, else ~/.local/share/kilo/auth.json."""
    data_home = environ.get("XDG_DATA_HOME") or str(Path(environ.get("HOME", Path.home())) / ".local" / "share")
    return Path(data_home) / "kilo" / "auth.json"


def resolve_gateway_credentials(
    environ: Mapping[str, str] = os.environ, auth_path: Path | None = None, now: float | None = None
) -> GatewayCredentials:
    """Return gateway credentials from KILO_API_KEY, else from the Kilo login.

    Raises:
        CredentialsError: neither is available, the login has expired, or
            KILO_BASE_URL isn't https (plain http is allowed only to a local
            proxy), or points away from Kilo while the key is the Kilo login.
    """
    base_url = environ.get("KILO_BASE_URL") or DEFAULT_GATEWAY_URL
    _check_base_url(base_url)
    if environ.get("KILO_API_KEY"):
        return GatewayCredentials(environ["KILO_API_KEY"], base_url, "KILO_API_KEY")

    auth_path = auth_path or kilo_auth_path(environ)
    entry = _kilo_login(auth_path)
    if entry is None:
        raise CredentialsError(
            "No Kilo Gateway credentials. Either set KILO_API_KEY in .env (create a key at "
            "https://app.kilo.ai under API Keys), or log in to Kilo with `kilo auth login`."
        )
    host = urlsplit(base_url).hostname or ""
    if host != KILO_HOST and not host.endswith(f".{KILO_HOST}"):
        raise CredentialsError(
            f"KILO_BASE_URL points at {host}, not Kilo, so your Kilo login won't be sent there. "
            "Set KILO_API_KEY to a key for that gateway instead."
        )
    if entry.get("type") == "api" and entry.get("key"):
        return GatewayCredentials(entry["key"], base_url, f"kilo login ({auth_path})")
    if entry.get("type") == "oauth" and entry.get("access"):
        if _expired(entry.get("expires"), now if now is not None else time.time()):
            raise CredentialsError(
                f"Your Kilo login in {auth_path} has expired. Run `kilo auth login` (or start Kilo once "
                "so it refreshes), or set KILO_API_KEY in .env."
            )
        return GatewayCredentials(entry["access"], base_url, f"kilo login ({auth_path})")
    raise CredentialsError(f"Unrecognised Kilo login in {auth_path}; set KILO_API_KEY in .env instead.")


def _check_base_url(base_url: str) -> None:
    """The key goes to base_url as a bearer token, so it must travel over https, or stay on this machine."""
    parts = urlsplit(base_url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
        return
    raise CredentialsError(f"KILO_BASE_URL must be an https URL (http only to localhost); got {base_url!r}")


def _kilo_login(auth_path: Path) -> dict | None:
    try:
        return json.loads(auth_path.read_text()).get("kilo")
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _expired(expires: float | None, now: float) -> bool:
    if not expires:
        return False
    expires_s = expires / 1000 if expires > 1e11 else expires  # Kilo stores milliseconds
    return expires_s <= now


def apply_to_environ(creds: GatewayCredentials, environ: MutableMapping[str, str] = os.environ) -> None:
    """Set KILO_API_KEY and KILO_BASE_URL for Inspect's `openai-api/kilo/...` models, without overwriting."""
    environ.setdefault("KILO_API_KEY", creds.api_key)
    environ.setdefault("KILO_BASE_URL", creds.base_url)


def main() -> int:
    """Report which credential source would be used; exit 1 if none."""
    try:
        creds = resolve_gateway_credentials()
    except CredentialsError as e:
        print(e)
        return 1
    print(f"Kilo Gateway credentials from {creds.source}; base URL {creds.base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
