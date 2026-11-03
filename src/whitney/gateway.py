"""Inspect model provider for the Kilo Gateway: `--model kilo/<gateway model id>`.

    inspect eval src/whitney/tasks.py@agents_md --model kilo/anthropic/claude-haiku-4.5

Credentials come from KILO_API_KEY or the user's Kilo login (see
`whitney.credentials`). The gateway is OpenAI-compatible, so this is
Inspect's OpenAI-compatible provider with the credentials filled in.
Registered through the `inspect_ai` entry point in pyproject.toml.
"""

from typing import Any

from inspect_ai.model import GenerateConfig
from inspect_ai.model._providers.openai_compatible import OpenAICompatibleAPI

from whitney.credentials import resolve_gateway_credentials


class KiloGatewayAPI(OpenAICompatibleAPI):
    """OpenAI-compatible model API pointed at the Kilo Gateway."""

    def __init__(
        self,
        model_name: str,
        base_url: str | None = None,
        api_key: str | None = None,
        config: GenerateConfig = GenerateConfig(),
        **model_args: Any,
    ) -> None:
        if api_key is None or base_url is None:
            creds = resolve_gateway_credentials()
            api_key = api_key or creds.api_key
            base_url = base_url or creds.base_url
        super().__init__(
            model_name=model_name,
            base_url=base_url,
            api_key=api_key,
            config=config,
            service="kilo",
            **model_args,
        )
