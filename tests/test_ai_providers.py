from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx
import openai
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.providers import factory as provider_factory
from app.ai.providers.base import (
    MAX_INSTRUCTIONS_LENGTH,
    ProviderErrorCode,
    StructuredGenerationError,
    StructuredGenerationRequest,
)
from app.ai.providers.factory import create_structured_generator
from app.ai.providers.openai import (
    OpenAIClient,
    OpenAIStructuredGenerator,
)
from app.consent import ConsentRepository, ConsentRequiredError
from app.database import initialize_database
from app.settings import load_ai_settings

PRIVATE_INPUT = "PRIVATE PROFILE BODY and PRIVATE JD BODY"
PRIVATE_INSTRUCTIONS = "Use the complete private sources exactly once."
PROVIDER_SECRET = "sk-secret-provider-error-private-profile"


class ExampleOutput(BaseModel):
    """Small strict output contract used by provider adapter tests."""

    model_config = ConfigDict(extra="forbid", strict=True)

    answer: str = Field(min_length=1, max_length=40)


@dataclass(frozen=True)
class FakeUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class FakeResponse:
    id: str
    output_text: str
    usage: FakeUsage
    status: str = "completed"
    service_tier: str = "default"


class FakeResponses:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "model": model,
                "input": input,
                "instructions": instructions,
                "text": text,
                "reasoning": reasoning,
                "store": store,
                "timeout": timeout,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


@dataclass
class FakeOpenAI:
    responses: FakeResponses


def response(
    answer: object,
    *,
    response_id: str = "resp_123",
    input_tokens: int = 12,
    output_tokens: int = 4,
) -> FakeResponse:
    return FakeResponse(
        id=response_id,
        output_text=json.dumps({"answer": answer}),
        usage=FakeUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        ),
    )


def request(
    *,
    contains_profile: bool = True,
    model: str = "configured-model",
    schema_name: str = "example_output",
    timeout_seconds: float = 90,
) -> StructuredGenerationRequest[ExampleOutput]:
    return StructuredGenerationRequest(
        model=model,
        schema_name=schema_name,
        schema=ExampleOutput,
        instructions=PRIVATE_INSTRUCTIONS,
        input_text=PRIVATE_INPUT,
        timeout_seconds=timeout_seconds,
        contains_profile=contains_profile,
    )


def generator(
    database_path: str, outcomes: list[object], *, grant: bool = True
) -> tuple[OpenAIStructuredGenerator, FakeResponses]:
    initialize_database(database_path)
    repository = ConsentRepository(database_path)
    if grant:
        repository.acknowledge_openai_profile_sharing(
            "2026-08-07T09:00:00+00:00"
        )
    responses = FakeResponses(outcomes)
    client = cast(OpenAIClient, FakeOpenAI(responses))
    return (
        OpenAIStructuredGenerator(
            client,
            repository,
            allowed_models={"configured-model"},
            reasoning_effort="high",
            request_timeout_seconds=90,
        ),
        responses,
    )


def test_openai_native_structured_success_uses_strict_schema(
    database_path: str,
) -> None:
    provider, fake = generator(database_path, [response("valid")])

    result = provider.generate(request())

    assert result.value == ExampleOutput(answer="valid")
    assert result.model == "configured-model"
    assert result.schema_name == "example_output"
    assert result.response_ids == ("resp_123",)
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 4
    assert result.usage.total_tokens == 16
    assert result.metadata.provider == "openai"
    assert result.metadata.statuses == ("completed",)
    assert result.metadata.service_tiers == ("default",)
    assert result.metadata.repair_attempted is False

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["model"] == "configured-model"
    assert call["input"] == PRIVATE_INPUT
    assert call["instructions"] == PRIVATE_INSTRUCTIONS
    assert call["timeout"] == 90
    assert call["store"] is False
    assert call["reasoning"] == {"effort": "high"}
    text = cast(Mapping[str, object], call["text"])
    output_format = cast(Mapping[str, object], text["format"])
    assert output_format["type"] == "json_schema"
    assert output_format["name"] == "example_output"
    assert output_format["strict"] is True
    assert output_format["schema"] == ExampleOutput.model_json_schema(
        mode="validation"
    )


def test_non_profile_request_does_not_require_profile_consent(
    database_path: str,
) -> None:
    provider, fake = generator(database_path, [response("public")], grant=False)

    result = provider.generate(request(contains_profile=False))

    assert result.value.answer == "public"
    assert len(fake.calls) == 1


def test_profile_request_is_blocked_before_provider_without_consent(
    database_path: str,
) -> None:
    provider, fake = generator(database_path, [response("unused")], grant=False)

    with pytest.raises(ConsentRequiredError, match="acknowledgement"):
        provider.generate(request())

    assert fake.calls == []


def test_consent_is_rechecked_before_a_profile_bearing_repair(
    database_path: str,
) -> None:
    def revoke_after_first_call() -> FakeResponse:
        ConsentRepository(database_path).revoke_openai_profile_sharing(
            "2026-08-07T10:00:00+00:00"
        )
        return response(42)

    provider, fake = generator(
        database_path,
        [revoke_after_first_call, response("must not be sent")],
    )

    with pytest.raises(ConsentRequiredError):
        provider.generate(request())

    assert len(fake.calls) == 1


def test_invalid_output_is_repaired_once_with_bounded_errors(
    database_path: str,
) -> None:
    invalid_raw = json.dumps(
        {"answer": 42, "leaked": "RAW PRIVATE PROVIDER RESPONSE"}
    )
    provider, fake = generator(
        database_path,
        [
            FakeResponse(
                id="resp_first",
                output_text=invalid_raw,
                usage=FakeUsage(10, 5, 15),
            ),
            response(
                "repaired",
                response_id="resp_second",
                input_tokens=14,
                output_tokens=6,
            ),
        ],
    )

    result = provider.generate(request())

    assert result.value.answer == "repaired"
    assert result.response_ids == ("resp_first", "resp_second")
    assert result.usage.input_tokens == 24
    assert result.usage.output_tokens == 11
    assert result.usage.total_tokens == 35
    assert result.metadata.repair_attempted is True
    assert len(fake.calls) == 2
    repair_instructions = cast(str, fake.calls[1]["instructions"])
    assert "failed local validation" in repair_instructions
    assert "answer" in repair_instructions
    assert len(repair_instructions) <= MAX_INSTRUCTIONS_LENGTH
    assert "RAW PRIVATE PROVIDER RESPONSE" not in repair_instructions
    assert fake.calls[1]["input"] == PRIVATE_INPUT


def test_invalid_output_gets_only_one_repair_and_safe_failure(
    database_path: str,
) -> None:
    first_raw = '{"answer": 123, "private": "first raw response"}'
    second_raw = '{"answer": 456, "private": "second raw response"}'
    provider, fake = generator(
        database_path,
        [
            FakeResponse("resp_1", first_raw, FakeUsage(1, 1, 2)),
            FakeResponse("resp_2", second_raw, FakeUsage(1, 1, 2)),
        ],
    )

    with pytest.raises(StructuredGenerationError) as caught:
        provider.generate(request())

    error = caught.value
    assert error.code is ProviderErrorCode.INVALID_OUTPUT
    assert error.retryable is False
    assert len(fake.calls) == 2
    rendered = f"{error!r} {error}"
    assert PRIVATE_INPUT not in rendered
    assert PRIVATE_INSTRUCTIONS not in rendered
    assert "first raw response" not in rendered
    assert "second raw response" not in rendered
    assert exception_chain(error) == [error]
    assert not any(
        isinstance(item, ValidationError) for item in exception_chain(error)
    )
    assert_exception_chain_redacts(
        error,
        PRIVATE_INPUT,
        PRIVATE_INSTRUCTIONS,
        first_raw,
        second_raw,
    )


def test_provider_metadata_is_allowlisted_and_redacted(
    database_path: str,
) -> None:
    provider, _ = generator(
        database_path,
        [
            FakeResponse(
                id="resp id PRIVATE PROFILE",
                output_text=json.dumps({"answer": "valid"}),
                usage=FakeUsage(1, 1, 2),
                status="completed PRIVATE JD",
                service_tier="default PRIVATE PROFILE",
            ),
            response("valid", response_id="resp_safe"),
        ],
    )

    result = provider.generate(request())

    assert result.response_ids == ("unavailable", "resp_safe")
    assert result.metadata.statuses == ("unknown", "completed")
    assert result.metadata.service_tiers == ("unknown", "default")
    assert PRIVATE_INPUT not in repr(result.metadata)


def _status_error(
    error_type: type[openai.APIStatusError], status: int
) -> openai.APIStatusError:
    request_value = httpx.Request("POST", "https://api.openai.com/v1")
    response_value = httpx.Response(
        status,
        request=request_value,
    )
    return error_type(
        PROVIDER_SECRET,
        response=response_value,
        body={"error": PROVIDER_SECRET},
    )


def exception_chain(error: BaseException) -> list[BaseException]:
    """Return every exception reachable through cause and context links."""
    pending = [error]
    seen: set[int] = set()
    chain: list[BaseException] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        chain.append(current)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
    return chain


def assert_exception_chain_redacts(
    error: BaseException, *private_values: str
) -> None:
    """Assert no exception in a chain renders private provider data."""
    rendered_chain = " ".join(
        f"{item!r} {item}" for item in exception_chain(error)
    )
    for private_value in private_values:
        assert private_value not in rendered_chain


def provider_errors() -> list[
    tuple[BaseException, ProviderErrorCode, bool, str]
]:
    request_value = httpx.Request("POST", "https://api.openai.com/v1")
    return [
        (
            openai.APITimeoutError(request=request_value),
            ProviderErrorCode.TIMEOUT,
            True,
            "The structured-generation request timed out.",
        ),
        (
            openai.RateLimitError(
                PROVIDER_SECRET,
                response=httpx.Response(
                    429,
                    request=request_value,
                ),
                body={"error": PROVIDER_SECRET},
            ),
            ProviderErrorCode.RATE_LIMIT,
            True,
            "The provider rate limit was reached.",
        ),
        (
            openai.APIConnectionError(
                message=PROVIDER_SECRET,
                request=request_value,
            ),
            ProviderErrorCode.CONNECTION,
            True,
            "The provider could not be reached.",
        ),
        (
            _status_error(openai.InternalServerError, 503),
            ProviderErrorCode.SERVER,
            True,
            "The provider reported a server error.",
        ),
        (
            _status_error(openai.AuthenticationError, 401),
            ProviderErrorCode.AUTHENTICATION,
            False,
            "The provider rejected its credentials.",
        ),
        (
            _status_error(openai.BadRequestError, 400),
            ProviderErrorCode.INVALID_REQUEST,
            False,
            "The provider rejected the structured-generation request.",
        ),
        (
            _status_error(openai.BadRequestError, 408),
            ProviderErrorCode.TIMEOUT,
            True,
            "The structured-generation request timed out.",
        ),
        (
            _status_error(openai.BadRequestError, 409),
            ProviderErrorCode.SERVER,
            True,
            "The provider reported a transient server error.",
        ),
    ]


@pytest.mark.parametrize(
    ("provider_error", "expected_code", "retryable", "safe_message"),
    provider_errors(),
)
def test_openai_errors_are_normalized_without_sensitive_details(
    database_path: str,
    provider_error: BaseException,
    expected_code: ProviderErrorCode,
    retryable: bool,
    safe_message: str,
) -> None:
    provider, _ = generator(database_path, [provider_error])

    with pytest.raises(StructuredGenerationError) as caught:
        provider.generate(request())

    error = caught.value
    assert error.code is expected_code
    assert error.retryable is retryable
    assert error.safe_message == safe_message
    assert exception_chain(error) == [error]
    rendered = f"{error!r} {error.safe_message}"
    assert PROVIDER_SECRET not in rendered
    assert PRIVATE_INPUT not in rendered
    assert PRIVATE_INSTRUCTIONS not in rendered
    assert_exception_chain_redacts(
        error,
        PROVIDER_SECRET,
        PRIVATE_INPUT,
        PRIVATE_INSTRUCTIONS,
    )


def test_repair_provider_error_has_no_raw_response_or_sdk_chain(
    database_path: str,
) -> None:
    first_raw = '{"answer": 123, "private": "first raw response"}'
    provider, fake = generator(
        database_path,
        [
            FakeResponse("resp_1", first_raw, FakeUsage(1, 1, 2)),
            _status_error(openai.InternalServerError, 503),
        ],
    )

    with pytest.raises(StructuredGenerationError) as caught:
        provider.generate(request())

    error = caught.value
    assert error.code is ProviderErrorCode.SERVER
    assert error.retryable is True
    assert len(fake.calls) == 2
    assert exception_chain(error) == [error]
    assert_exception_chain_redacts(
        error,
        first_raw,
        PROVIDER_SECRET,
        PRIVATE_INPUT,
        PRIVATE_INSTRUCTIONS,
    )


@pytest.mark.parametrize(
    ("model", "timeout_seconds"),
    [("not-configured", 90.0), ("configured-model", 89.0)],
)
def test_adapter_rejects_unconfigured_model_or_timeout_before_call(
    database_path: str, model: str, timeout_seconds: float
) -> None:
    provider, fake = generator(database_path, [response("unused")])
    generation_request = request(model=model, timeout_seconds=timeout_seconds)

    with pytest.raises(StructuredGenerationError) as caught:
        provider.generate(generation_request)

    assert caught.value.code is ProviderErrorCode.CONFIGURATION
    assert caught.value.retryable is False
    assert fake.calls == []


def test_factory_rejects_missing_environment_credential(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with pytest.raises(StructuredGenerationError) as caught:
        create_structured_generator(
            load_ai_settings(), database_path, environ={}
        )

    assert caught.value.code is ProviderErrorCode.CONFIGURATION
    assert caught.value.retryable is False
    assert "credential" in caught.value.safe_message


def test_factory_reads_credential_from_environment_only(
    database_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_database(database_path)
    constructed: dict[str, object] = {}

    def fake_client(**kwargs: object) -> FakeOpenAI:
        constructed.update(kwargs)
        return FakeOpenAI(FakeResponses([]))

    monkeypatch.setattr(provider_factory, "OpenAI", fake_client)

    result = create_structured_generator(
        load_ai_settings(),
        database_path,
        environ={"OPENAI_API_KEY": "environment-value"},
    )

    assert isinstance(result, OpenAIStructuredGenerator)
    assert constructed == {
        "api_key": "environment-value",
        "base_url": "https://api.openai.com/v1",
        "max_retries": 0,
    }


def test_factory_pins_openai_base_url_despite_environment_override(
    database_path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent an inherited SDK endpoint override from receiving private data."""
    initialize_database(database_path)
    constructed: dict[str, object] = {}

    def fake_client(**kwargs: object) -> FakeOpenAI:
        constructed.update(kwargs)
        return FakeOpenAI(FakeResponses([]))

    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.example/v1")
    monkeypatch.setattr(provider_factory, "OpenAI", fake_client)

    create_structured_generator(
        load_ai_settings(),
        database_path,
        environ={"OPENAI_API_KEY": "environment-value"},
    )

    assert constructed["base_url"] == "https://api.openai.com/v1"


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: StructuredGenerationRequest(
            model=" padded ",
            schema_name="example_output",
            schema=ExampleOutput,
            instructions="instructions",
            input_text="input",
            timeout_seconds=90,
            contains_profile=False,
        ),
        lambda: StructuredGenerationRequest(
            model="configured-model",
            schema_name="not safe",
            schema=ExampleOutput,
            instructions="instructions",
            input_text="input",
            timeout_seconds=90,
            contains_profile=False,
        ),
        lambda: StructuredGenerationRequest(
            model="configured-model",
            schema_name="example_output",
            schema=ExampleOutput,
            instructions="",
            input_text="input",
            timeout_seconds=90,
            contains_profile=False,
        ),
        lambda: StructuredGenerationRequest(
            model="configured-model",
            schema_name="example_output",
            schema=ExampleOutput,
            instructions="instructions",
            input_text="",
            timeout_seconds=90,
            contains_profile=False,
        ),
        lambda: StructuredGenerationRequest(
            model="configured-model",
            schema_name="example_output",
            schema=ExampleOutput,
            instructions="instructions",
            input_text="input",
            timeout_seconds=301,
            contains_profile=False,
        ),
    ],
)
def test_structured_request_rejects_invalid_values(
    request_factory: Callable[[], StructuredGenerationRequest[ExampleOutput]],
) -> None:
    with pytest.raises(ValueError):
        request_factory()


@pytest.mark.parametrize(
    "schema_name",
    ["example_output", "example-output", "A1_b-2"],
)
def test_structured_request_accepts_openai_compatible_schema_names(
    schema_name: str,
) -> None:
    generation_request = request(schema_name=schema_name)

    assert generation_request.schema_name == schema_name


@pytest.mark.parametrize(
    "schema_name",
    ["", "naïve", "١", "not safe", "not.safe", "x" * 65],
)
def test_structured_request_rejects_invalid_schema_name_before_provider_call(
    database_path: str, schema_name: str
) -> None:
    _, fake = generator(database_path, [response("unused")], grant=False)

    with pytest.raises(ValueError, match="ASCII letters"):
        request(schema_name=schema_name)

    assert fake.calls == []


def test_factory_uses_database_path_without_reading_private_files(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "private" / "jobhunter.db"
    initialize_database(database_path)

    with pytest.raises(StructuredGenerationError):
        create_structured_generator(
            load_ai_settings(), database_path, environ={}
        )
