"""OpenAI Responses API adapter for locally validated structured output."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

import openai
from pydantic import BaseModel, ValidationError

from app.ai.providers.base import (
    MAX_INSTRUCTIONS_LENGTH,
    MAX_RESPONSE_IDENTIFIER_LENGTH,
    ProviderErrorCode,
    ProviderMetadata,
    StructuredGenerationError,
    StructuredGenerationRequest,
    StructuredGenerationResult,
    TokenUsage,
)
from app.consent import ConsentRepository

MAX_REPAIR_ERRORS = 8
MAX_REPAIR_ERROR_LENGTH = 160
MAX_REPAIR_SUMMARY_LENGTH = 1_500
SAFE_METADATA_PATTERN = re.compile(r"^[A-Za-z0-9_.:/-]+$")


class ResponsesClient(Protocol):
    """Subset of the OpenAI client used by this adapter."""

    def create(
        self,
        *,
        model: str,
        input: str,
        instructions: str,
        text: Mapping[str, object],
        reasoning: Mapping[str, str],
        store: bool,
        timeout: float,
    ) -> object:
        """Create one non-streaming response."""


class OpenAIClient(Protocol):
    """Injectable client surface used by deterministic provider tests."""

    responses: ResponsesClient


@dataclass(frozen=True)
class _ResponseRecord:
    response_id: str
    status: str
    service_tier: str
    usage: TokenUsage


class _InvalidOutput(ValueError):
    def __init__(self, summary: str) -> None:
        super().__init__("Structured provider output was invalid.")
        self.summary = summary


class OpenAIStructuredGenerator:
    """Generate strict JSON with one bounded local-validation repair."""

    def __init__(
        self,
        client: OpenAIClient,
        consent_repository: ConsentRepository,
        *,
        allowed_models: Collection[str] | None = None,
        reasoning_effort: Literal[
            "none", "low", "medium", "high", "xhigh", "max"
        ],
        request_timeout_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._consent_repository = consent_repository
        self._allowed_models = (
            frozenset(allowed_models) if allowed_models is not None else None
        )
        self._reasoning_effort = reasoning_effort
        self._request_timeout_seconds = request_timeout_seconds

    def generate[OutputT: BaseModel](
        self, request: StructuredGenerationRequest[OutputT]
    ) -> StructuredGenerationResult[OutputT]:
        """Return validated output or a sanitized, classified failure."""
        self._validate_configuration(request)

        responses: list[_ResponseRecord] = []
        first = self._request(request, request.instructions)
        responses.append(_response_record(first))
        first_validation_summary: str | None = None
        try:
            value = _validate_output(first, request.schema)
        except _InvalidOutput as first_error:
            first_validation_summary = first_error.summary

        if first_validation_summary is not None:
            repaired = True
            repair_instructions = _repair_instructions(
                request.instructions, first_validation_summary
            )
            second = self._request(request, repair_instructions)
            responses.append(_response_record(second))
            repair_failed = False
            try:
                value = _validate_output(second, request.schema)
            except _InvalidOutput:
                repair_failed = True
            if repair_failed:
                raise StructuredGenerationError(
                    ProviderErrorCode.INVALID_OUTPUT,
                    "The provider returned invalid structured output after "
                    "one repair attempt.",
                    retryable=False,
                )
        else:
            repaired = False

        usage = TokenUsage()
        for response in responses:
            usage += response.usage
        return StructuredGenerationResult(
            value=value,
            model=request.model,
            schema_name=request.schema_name,
            response_ids=tuple(item.response_id for item in responses),
            usage=usage,
            metadata=ProviderMetadata(
                provider="openai",
                statuses=tuple(item.status for item in responses),
                service_tiers=tuple(item.service_tier for item in responses),
                repair_attempted=repaired,
            ),
        )

    def _validate_configuration[OutputT: BaseModel](
        self, request: StructuredGenerationRequest[OutputT]
    ) -> None:
        if (
            self._allowed_models is not None
            and request.model not in self._allowed_models
        ):
            raise StructuredGenerationError(
                ProviderErrorCode.CONFIGURATION,
                "The requested model is not configured for this provider.",
                retryable=False,
            )
        if (
            self._request_timeout_seconds is not None
            and request.timeout_seconds != self._request_timeout_seconds
        ):
            raise StructuredGenerationError(
                ProviderErrorCode.CONFIGURATION,
                "The requested timeout does not match provider settings.",
                retryable=False,
            )

    def _request[OutputT: BaseModel](
        self,
        request: StructuredGenerationRequest[OutputT],
        instructions: str,
    ) -> object:
        if request.contains_profile:
            self._consent_repository.require_openai_profile_sharing()
        response_format: Mapping[str, object] = {
            "format": {
                "type": "json_schema",
                "name": request.schema_name,
                "strict": True,
                "schema": request.schema.model_json_schema(mode="validation"),
            }
        }
        normalized_error: StructuredGenerationError | None = None
        try:
            return self._client.responses.create(
                model=request.model,
                input=request.input_text,
                instructions=instructions,
                text=response_format,
                reasoning={"effort": self._reasoning_effort},
                store=False,
                timeout=request.timeout_seconds,
            )
        except openai.APITimeoutError:
            normalized_error = _provider_error(
                ProviderErrorCode.TIMEOUT,
                "The structured-generation request timed out.",
                retryable=True,
            )
        except openai.RateLimitError:
            normalized_error = _provider_error(
                ProviderErrorCode.RATE_LIMIT,
                "The provider rate limit was reached.",
                retryable=True,
            )
        except openai.APIConnectionError:
            normalized_error = _provider_error(
                ProviderErrorCode.CONNECTION,
                "The provider could not be reached.",
                retryable=True,
            )
        except openai.AuthenticationError:
            normalized_error = _provider_error(
                ProviderErrorCode.AUTHENTICATION,
                "The provider rejected its credentials.",
                retryable=False,
            )
        except openai.InternalServerError:
            normalized_error = _provider_error(
                ProviderErrorCode.SERVER,
                "The provider reported a server error.",
                retryable=True,
            )
        except openai.APIStatusError as error:
            normalized_error = _status_error(error.status_code)
        except openai.OpenAIError:
            normalized_error = _provider_error(
                ProviderErrorCode.INVALID_REQUEST,
                "The provider rejected the structured-generation request.",
                retryable=False,
            )
        if normalized_error is not None:
            raise normalized_error
        raise AssertionError("provider error normalization did not run")


def _status_error(status_code: int) -> StructuredGenerationError:
    if status_code == 408:
        return _provider_error(
            ProviderErrorCode.TIMEOUT,
            "The structured-generation request timed out.",
            retryable=True,
        )
    if status_code == 429:
        return _provider_error(
            ProviderErrorCode.RATE_LIMIT,
            "The provider rate limit was reached.",
            retryable=True,
        )
    if status_code == 409:
        return _provider_error(
            ProviderErrorCode.SERVER,
            "The provider reported a transient server error.",
            retryable=True,
        )
    if status_code >= 500:
        return _provider_error(
            ProviderErrorCode.SERVER,
            "The provider reported a server error.",
            retryable=True,
        )
    if status_code in {401, 403}:
        return _provider_error(
            ProviderErrorCode.AUTHENTICATION,
            "The provider rejected its credentials.",
            retryable=False,
        )
    return _provider_error(
        ProviderErrorCode.INVALID_REQUEST,
        "The provider rejected the structured-generation request.",
        retryable=False,
    )


def _provider_error(
    code: ProviderErrorCode, safe_message: str, *, retryable: bool
) -> StructuredGenerationError:
    return StructuredGenerationError(code, safe_message, retryable=retryable)


def _validate_output[OutputT: BaseModel](
    response: object, schema: type[OutputT]
) -> OutputT:
    status = getattr(response, "status", None)
    if status is not None and status != "completed":
        raise _InvalidOutput("response: incomplete")
    output_text = getattr(response, "output_text", None)
    if not isinstance(output_text, str) or not output_text.strip():
        raise _InvalidOutput("response: missing structured content")
    validation_summary: str | None = None
    try:
        value = schema.model_validate_json(output_text)
    except ValidationError as error:
        validation_summary = _validation_summary(error)
    if validation_summary is not None:
        raise _InvalidOutput(validation_summary)
    return value


def _validation_summary(error: ValidationError) -> str:
    summaries: list[str] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    )[:MAX_REPAIR_ERRORS]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        error_type = str(item.get("type", "validation_error"))
        message = str(item.get("msg", "invalid value"))
        message = " ".join(message.split())[:MAX_REPAIR_ERROR_LENGTH]
        summaries.append(f"{location or 'root'}: {error_type}: {message}")
    summary = "\n".join(summaries)
    return summary[:MAX_REPAIR_SUMMARY_LENGTH] or "root: validation_error"


def _repair_instructions(instructions: str, summary: str) -> str:
    repair = (
        "\n\nThe previous structured output failed local validation. "
        "Generate the complete result again from the supplied input and "
        "correct these bounded validation errors:\n"
        f"{summary[:MAX_REPAIR_SUMMARY_LENGTH]}"
    )
    maximum_original = MAX_INSTRUCTIONS_LENGTH - len(repair)
    return instructions[:maximum_original] + repair


def _response_record(response: object) -> _ResponseRecord:
    return _ResponseRecord(
        response_id=_safe_metadata(
            getattr(response, "id", None),
            "unavailable",
            MAX_RESPONSE_IDENTIFIER_LENGTH,
        ),
        status=_safe_metadata(getattr(response, "status", None), "unknown", 32),
        service_tier=_safe_metadata(
            getattr(response, "service_tier", None), "unknown", 32
        ),
        usage=_token_usage(getattr(response, "usage", None)),
    )


def _safe_metadata(value: object, fallback: str, maximum: int) -> str:
    if (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and SAFE_METADATA_PATTERN.fullmatch(value)
    ):
        return value
    return fallback


def _token_usage(value: object) -> TokenUsage:
    return TokenUsage(
        input_tokens=_nonnegative_int(getattr(value, "input_tokens", None)),
        output_tokens=_nonnegative_int(getattr(value, "output_tokens", None)),
        total_tokens=_nonnegative_int(getattr(value, "total_tokens", None)),
    )


def _nonnegative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0
