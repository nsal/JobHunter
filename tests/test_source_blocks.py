from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from app.ai.source_blocks import (
    MAX_BLOCK_CHARS,
    MAX_SOURCE_BLOCKS,
    MAX_SOURCE_BYTES,
    BlockKind,
    SourceBlock,
    SourceBlockError,
    SourceKind,
    parse_source_blocks,
)


def test_markdown_structure_produces_ordered_stable_blocks() -> None:
    source = """# Candidate

Builds typed services.

- Python
- FastAPI

| Role | Years |
| --- | ---: |
| Engineer | 5 |
"""

    first = parse_source_blocks(source, SourceKind.PROFILE)
    second = parse_source_blocks(source, SourceKind.PROFILE)

    assert first == second
    assert [block.block_id for block in first] == [
        "profile-0001",
        "profile-0002",
        "profile-0003",
        "profile-0004",
    ]
    assert [block.order for block in first] == [1, 2, 3, 4]
    assert [block.kind for block in first] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.LIST,
        BlockKind.TABLE,
    ]
    assert first[2].content == "- Python\n- FastAPI"
    assert first[0].content_sha256 == hashlib.sha256(b"# Candidate").hexdigest()


def test_plain_jd_bytes_and_markdown_code_are_supported() -> None:
    source = b"Role overview\n\nDeliver APIs.\n\n```text\nPython 3.14\n```\n"

    blocks = parse_source_blocks(source, SourceKind.JOB_DESCRIPTION)

    assert [block.block_id for block in blocks] == [
        "jd-0001",
        "jd-0002",
        "jd-0003",
    ]
    assert [block.kind for block in blocks] == [
        BlockKind.PARAGRAPH,
        BlockKind.PARAGRAPH,
        BlockKind.CODE,
    ]


def test_canonical_unicode_and_line_endings_have_stable_content_hashes() -> (
    None
):
    decomposed = "# Re\u0301sume\u0301\r\n\r\nPython\r\n"
    composed = "# Résumé\n\nPython\n"

    first = parse_source_blocks(decomposed, SourceKind.PROFILE)
    second = parse_source_blocks(composed, SourceKind.PROFILE)

    assert first == second


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
def test_unicode_separators_remain_source_content(separator: str) -> None:
    source = f"Before{separator}after"

    blocks = parse_source_blocks(source, SourceKind.PROFILE)

    assert len(blocks) == 1
    assert blocks[0].content == source
    assert (
        blocks[0].content_sha256
        == hashlib.sha256(source.encode("utf-8")).hexdigest()
    )


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize(
    "separator_offset", [MAX_BLOCK_CHARS - 1, MAX_BLOCK_CHARS]
)
def test_unicode_separators_survive_block_boundaries(
    separator: str, separator_offset: int
) -> None:
    source = "a" * separator_offset + separator + "b" * 10

    blocks = parse_source_blocks(source, SourceKind.PROFILE)

    assert len(blocks) == 2
    assert "".join(block.content for block in blocks) == source
    assert separator in {blocks[0].content[-1], blocks[1].content[0]}
    assert all(
        block.content_sha256
        == hashlib.sha256(block.content.encode("utf-8")).hexdigest()
        for block in blocks
    )


@pytest.mark.parametrize("line_ending", ["\n", "\r\n", "\r"])
def test_line_endings_normalize_to_stable_source_blocks(
    line_ending: str,
) -> None:
    source = f"# Candidate{line_ending}{line_ending}Builds typed services."

    blocks = parse_source_blocks(source, SourceKind.PROFILE)

    assert [block.content for block in blocks] == [
        "# Candidate",
        "Builds typed services.",
    ]


def test_long_content_is_split_into_bounded_deterministic_blocks() -> None:
    source = "word " * (MAX_BLOCK_CHARS // 2)

    blocks = parse_source_blocks(source, SourceKind.JOB_DESCRIPTION)

    assert len(blocks) > 1
    assert all(1 <= len(block.content) <= MAX_BLOCK_CHARS for block in blocks)
    assert [block.order for block in blocks] == list(range(1, len(blocks) + 1))


def test_source_block_round_trip_revalidates_identity_and_hash() -> None:
    block = parse_source_blocks("# Profile", SourceKind.PROFILE)[0]

    restored = SourceBlock.model_validate_json(block.model_dump_json())
    assert restored == block

    changed = block.model_dump()
    changed["content"] = "# Different"
    with pytest.raises(ValidationError, match="hash does not match"):
        SourceBlock.model_validate(changed)

    changed = block.model_dump()
    changed["block_id"] = "profile-0002"
    with pytest.raises(ValidationError, match="does not match source kind"):
        SourceBlock.model_validate(changed)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"\xff\xfe", "not valid UTF-8"),
        ("", "between 1"),
        ("   \n", "at least one text block"),
        ("\u0085\u2028\u2029", "at least one text block"),
        ("valid\x00invalid", "control characters"),
        ("x" * (MAX_SOURCE_BYTES + 1), "between 1"),
    ],
)
def test_invalid_or_oversized_source_is_rejected(
    source: str | bytes, message: str
) -> None:
    with pytest.raises(SourceBlockError, match=message):
        parse_source_blocks(source, SourceKind.PROFILE)


def test_excessive_block_count_is_rejected() -> None:
    source = "\n\n".join(
        f"# Heading {number}" for number in range(MAX_SOURCE_BLOCKS + 1)
    )

    with pytest.raises(SourceBlockError, match="more than 512 blocks"):
        parse_source_blocks(source, SourceKind.PROFILE)
