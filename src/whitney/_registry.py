"""Registers this package's Inspect extensions (loaded via the `inspect_ai` entry point).

Importing the tasks registers them as `whitney/<name>`, so
`inspect eval whitney/agents_md` works once the package is installed.
"""

from inspect_ai.model import modelapi

from whitney.tasks import agents_md, kilo_jsonc, scenario_eval

__all__ = ["agents_md", "kilo_gateway", "kilo_jsonc", "scenario_eval"]


@modelapi(name="kilo")
def kilo_gateway():
    """The Kilo Gateway model provider, imported lazily so Inspect starts fast."""
    from whitney.gateway import KiloGatewayAPI

    return KiloGatewayAPI
