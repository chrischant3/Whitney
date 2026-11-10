"""Registers this package's Inspect extensions (loaded via the `inspect_ai` entry point)."""

from inspect_ai.model import modelapi


@modelapi(name="kilo")
def kilo_gateway():
    """The Kilo Gateway model provider, imported lazily so Inspect starts fast."""
    from whitney.gateway import KiloGatewayAPI

    return KiloGatewayAPI


# Importing the tasks registers them as whitney/<name>, so `inspect eval whitney/agents_md` works once the
# package is installed. Below the provider, which is then registered even if this import ever fails.
from whitney.tasks import agents_md, kilo_jsonc, scenario_eval

__all__ = ["agents_md", "kilo_gateway", "kilo_jsonc", "scenario_eval"]
