"""Construction of configured structured-generation providers."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from openai import OpenAI

from app.ai.providers.base import (
    ProviderErrorCode,
    StructuredGenerationError,
    StructuredGenerator,
)
from app.ai.providers.openai import OpenAIClient, OpenAIStructuredGenerator
from app.consent import ConsentRepository
from app.settings import AiSettings

OPENAI_API_BASE_URL = "https://api.openai.com/v1"


def is_openai_credential_ready(
    value: str | Mapping[str, str] | None,
) -> bool:
    """Return whether an OpenAI credential is present without padding."""
    credential = (
        value.get("OPENAI_API_KEY", "") if isinstance(value, Mapping) else value
    )
    if not credential:
        return False
    return credential == credential.strip()


def create_structured_generator(
    settings: AiSettings,
    database_path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> StructuredGenerator:
    """Build the selected provider using environment-only credentials."""
    environment = os.environ if environ is None else environ
    if settings.provider.name != "openai":
        raise StructuredGenerationError(
            ProviderErrorCode.CONFIGURATION,
            "The configured structured-generation provider is unsupported.",
            retryable=False,
        )
    api_key = environment.get("OPENAI_API_KEY", "")
    if not is_openai_credential_ready(api_key):
        raise StructuredGenerationError(
            ProviderErrorCode.CONFIGURATION,
            "The OpenAI API credential is not configured.",
            retryable=False,
        )
    client = cast(
        OpenAIClient,
        OpenAI(
            api_key=api_key,
            base_url=OPENAI_API_BASE_URL,
            max_retries=0,
        ),
    )
    return OpenAIStructuredGenerator(
        client,
        ConsentRepository(database_path),
        allowed_models={
            settings.provider.assessment_model,
            settings.provider.cv_model,
        },
        reasoning_effort=settings.provider.reasoning_effort,
        request_timeout_seconds=(settings.provider.request_timeout_seconds),
    )
