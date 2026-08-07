"""Provider-neutral contracts for schema-constrained AI generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

MAX_SCHEMA_NAME_LENGTH = 64
MAX_MODEL_NAME_LENGTH = 100
MAX_INSTRUCTIONS_LENGTH = 32_000
MAX_INPUT_LENGTH = 500_000
MAX_RESPONSE_IDENTIFIER_LENGTH = 200
SCHEMA_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class ProviderErrorCode(StrEnum):
    """Stable error classes consumed by durable retry handling."""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    CONNECTION = "connection"
    SERVER = "server"
    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    INVALID_OUTPUT = "invalid_output"
    CONFIGURATION = "configuration"


class StructuredGenerationError(RuntimeError):
    """A sanitized provider failure with deterministic retry semantics."""

    def __init__(
        self,
        code: ProviderErrorCode,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


@dataclass(frozen=True)
class StructuredGenerationRequest[OutputT: BaseModel]:
    """One bounded, typed request for schema-constrained output."""

    model: str
    schema_name: str
    schema: type[OutputT]
    instructions: str
    input_text: str
    timeout_seconds: float
    contains_profile: bool

    def __post_init__(self) -> None:
        """Reject invalid request values before private data can be sent."""
        if not self.model or self.model != self.model.strip():
            raise ValueError("model must be non-empty trimmed text")
        if len(self.model) > MAX_MODEL_NAME_LENGTH:
            raise ValueError("model is too long")
        if (
            not self.schema_name
            or len(self.schema_name) > MAX_SCHEMA_NAME_LENGTH
            or SCHEMA_NAME_PATTERN.fullmatch(self.schema_name) is None
        ):
            raise ValueError(
                "schema name must be 1 to 64 ASCII letters, digits, "
                "underscores, or dashes"
            )
        if not self.instructions or len(self.instructions) > (
            MAX_INSTRUCTIONS_LENGTH
        ):
            raise ValueError("instructions must be non-empty and bounded")
        if not self.input_text or len(self.input_text) > MAX_INPUT_LENGTH:
            raise ValueError("input text must be non-empty and bounded")
        if not 1 <= self.timeout_seconds <= 300:
            raise ValueError("timeout must be between 1 and 300 seconds")


@dataclass(frozen=True)
class TokenUsage:
    """Provider-neutral token accounting for one logical generation."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.total_tokens) < 0:
            raise ValueError("token usage cannot be negative")

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True)
class ProviderMetadata:
    """Small allowlist of safe provider response metadata."""

    provider: str
    statuses: tuple[str, ...]
    service_tiers: tuple[str, ...]
    repair_attempted: bool


@dataclass(frozen=True)
class StructuredGenerationResult[OutputT: BaseModel]:
    """Locally validated output plus sanitized operational metadata."""

    value: OutputT
    model: str
    schema_name: str
    response_ids: tuple[str, ...]
    usage: TokenUsage
    metadata: ProviderMetadata


class StructuredGenerator(Protocol):
    """Narrow interface implemented by structured-generation providers."""

    def generate[OutputT: BaseModel](
        self, request: StructuredGenerationRequest[OutputT]
    ) -> StructuredGenerationResult[OutputT]:
        """Generate and locally validate one structured result."""
