"""Registers this package's Inspect extensions (loaded via the `inspect_ai` entry point)."""

from inspect_ai.model import modelapi


@modelapi(name="kilo")
def kilo_gateway():
    """The Kilo Gateway model provider, imported lazily so Inspect starts fast."""
    from whitney.gateway import KiloGatewayAPI

    return KiloGatewayAPI
