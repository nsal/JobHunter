"""Deterministic bounded source blocks for profiles and job descriptions."""

from __future__ import annotations

import hashlib
import unicodedata
from enum import StrEnum
from typing import Self

from markdown_it import MarkdownIt
from markdown_it.token import Token
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SOURCE_BYTES = 1024 * 1024
MAX_BLOCK_CHARS = 4_000
MAX_SOURCE_BLOCKS = 512
PRESERVED_UNICODE_SEPARATORS = frozenset({"\u0085", "\u2028", "\u2029"})


class SourceBlockError(ValueError):
    """Raised when source text cannot become safe bounded blocks."""


class SourceKind(StrEnum):
    """Supported private or application source documents."""

    PROFILE = "profile"
    JOB_DESCRIPTION = "job_description"


class BlockKind(StrEnum):
    """Structural Markdown forms retained for model context."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    CODE = "code"


class SourceBlock(BaseModel):
    """One ordered, content-hashed source fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_id: str = Field(pattern=r"^(?:profile|jd)-[0-9]{4}$")
    order: int = Field(ge=1, le=MAX_SOURCE_BLOCKS)
    source_kind: SourceKind
    kind: BlockKind
    content: str = Field(min_length=1, max_length=MAX_BLOCK_CHARS)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def identity_and_hash_are_consistent(self) -> Self:
        """Prevent a serialized block from lying about its identity/content."""
        prefix = "profile" if self.source_kind is SourceKind.PROFILE else "jd"
        expected_id = f"{prefix}-{self.order:04d}"
        if self.block_id != expected_id:
            raise ValueError("block ID does not match source kind and order")
        if self.content != _strip_block_padding(self.content):
            raise ValueError(
                "block content must not have surrounding whitespace"
            )
        if _sha256(self.content) != self.content_sha256:
            raise ValueError("block content hash does not match content")
        return self


def parse_source_blocks(
    source: str | bytes, source_kind: SourceKind
) -> tuple[SourceBlock, ...]:
    """Parse UTF-8 Markdown/plain text into deterministic bounded blocks."""
    text = _decode_and_normalize(source)
    if not text.strip():
        raise SourceBlockError("source must contain at least one text block")
    lines = text.split("\n")
    markdown = MarkdownIt("commonmark").enable("table")
    tokens = markdown.parse(text)
    fragments = _structural_fragments(tokens, lines)
    if not fragments:
        raise SourceBlockError("source must contain at least one text block")

    bounded = tuple(
        (kind, part)
        for kind, content in fragments
        for part in _split_content(content)
    )
    if len(bounded) > MAX_SOURCE_BLOCKS:
        raise SourceBlockError(
            f"source produces more than {MAX_SOURCE_BLOCKS} blocks"
        )

    prefix = "profile" if source_kind is SourceKind.PROFILE else "jd"
    return tuple(
        SourceBlock(
            block_id=f"{prefix}-{order:04d}",
            order=order,
            source_kind=source_kind,
            kind=kind,
            content=content,
            content_sha256=_sha256(content),
        )
        for order, (kind, content) in enumerate(bounded, start=1)
    )


def _decode_and_normalize(source: str | bytes) -> str:
    if isinstance(source, bytes):
        size = len(source)
        try:
            decoded = source.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise SourceBlockError("source is not valid UTF-8") from error
    else:
        try:
            encoded = source.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise SourceBlockError("source contains invalid Unicode") from error
        size = len(encoded)
        decoded = source
    if size == 0 or size > MAX_SOURCE_BYTES:
        raise SourceBlockError(
            f"source must be between 1 and {MAX_SOURCE_BYTES} UTF-8 bytes"
        )
    normalized = unicodedata.normalize("NFC", decoded).replace("\r\n", "\n")
    normalized = normalized.replace("\r", "\n")
    if (
        any(
            ord(character) < 32 and character not in "\n\t"
            for character in normalized
        )
        or "\x7f" in normalized
    ):
        raise SourceBlockError("source contains unsupported control characters")
    return normalized


def _structural_fragments(
    tokens: list[Token], lines: list[str]
) -> tuple[tuple[BlockKind, str], ...]:
    starts = {
        "heading_open": BlockKind.HEADING,
        "paragraph_open": BlockKind.PARAGRAPH,
        "bullet_list_open": BlockKind.LIST,
        "ordered_list_open": BlockKind.LIST,
        "table_open": BlockKind.TABLE,
        "fence": BlockKind.CODE,
        "code_block": BlockKind.CODE,
    }
    fragments: list[tuple[BlockKind, str]] = []
    covered_until = 0
    for token in tokens:
        if token.type not in starts or token.map is None:
            continue
        start, end = token.map
        if start < covered_until:
            continue
        content = _strip_block_padding("\n".join(lines[start:end]))
        if not content:
            continue
        fragments.append((starts[token.type], content))
        covered_until = end
    return tuple(fragments)


def _split_content(content: str) -> tuple[str, ...]:
    remaining = _strip_block_padding(content)
    parts: list[str] = []
    while len(remaining) > MAX_BLOCK_CHARS:
        split_at = _preferred_split(remaining[: MAX_BLOCK_CHARS + 1])
        part = _strip_block_padding(remaining[:split_at])
        if part:
            parts.append(part)
        remaining = _strip_block_padding(remaining[split_at:])
    if remaining:
        parts.append(remaining)
    return tuple(parts)


def _preferred_split(window: str) -> int:
    minimum = MAX_BLOCK_CHARS // 2
    for separator in ("\n", " "):
        split_at = window.rfind(separator, minimum, MAX_BLOCK_CHARS + 1)
        if split_at >= minimum:
            return split_at + (1 if separator == "\n" else 0)
    return MAX_BLOCK_CHARS


def _strip_block_padding(content: str) -> str:
    """Trim whitespace without discarding preserved Unicode separators."""
    start = 0
    end = len(content)
    while (
        start < end
        and content[start].isspace()
        and content[start] not in PRESERVED_UNICODE_SEPARATORS
    ):
        start += 1
    while (
        end > start
        and content[end - 1].isspace()
        and content[end - 1] not in PRESERVED_UNICODE_SEPARATORS
    ):
        end -= 1
    return content[start:end]


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
