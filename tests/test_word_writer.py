from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, Mm

from app.ai.schema_models import CvContent
from app.artefacts import ArtefactStore, UnsafeArtefactPathError
from app.documents.word_writer import (
    WordWriter,
    WordWriterError,
    candidate_filename,
)
from app.settings import CvLayoutSettings


def layout() -> CvLayoutSettings:
    return CvLayoutSettings.model_validate(
        {
            "page": {
                "size": "A4",
                "orientation": "portrait",
                "margins": {
                    "top": 0.5,
                    "right": 0.55,
                    "bottom": 0.5,
                    "left": 0.55,
                },
            },
            "fonts": {
                "body": "Aptos",
                "headings": "Aptos Display",
                "body_size_pt": 9.5,
                "heading_size_pt": 12,
            },
            "spacing": {
                "line": 1.0,
                "paragraph_after_pt": 3,
                "section_after_pt": 6,
            },
            "styles": {
                "heading_color": "1F2937",
                "accent_color": "2563EB",
                "bullet_indent_inches": 0.2,
            },
            "output": {"filename": "Tailored CV.docx"},
        }
    )


def content(**changes: Any) -> CvContent:
    values: dict[str, Any] = {
        "schema_version": "v1",
        "identity": {
            "full_name": {
                "text": "Avery Morgan",
                "profile_block_ids": ["profile-0001"],
            },
            "professional_title": {
                "text": "Software Engineer",
                "profile_block_ids": ["profile-0002"],
            },
            "email": {
                "text": "avery@example.test",
                "profile_block_ids": ["profile-0002"],
            },
            "phone": None,
            "location": None,
            "website": {
                "text": "https://example.test/avery",
                "profile_block_ids": ["profile-0002"],
            },
        },
        "sections": [
            {
                "section_id": "section-01",
                "kind": "summary",
                "heading": "Profile",
                "claims": [
                    {
                        "claim_id": "claim-001",
                        "text": "Builds typed Python services.",
                        "profile_block_ids": ["profile-0002"],
                        "requirement_ids": ["req-001"],
                    }
                ],
            },
            {
                "section_id": "section-02",
                "kind": "skills",
                "heading": "Skills",
                "claims": [
                    {
                        "claim_id": "claim-002",
                        "text": "Python, FastAPI",
                        "profile_block_ids": ["profile-0002"],
                        "requirement_ids": ["req-001"],
                    }
                ],
            },
        ],
    }
    values.update(changes)
    return CvContent.model_validate_json(json.dumps(values))


def template(path: Path) -> None:
    document = Document()
    document.add_paragraph("Private placeholder that must be removed.")
    document.sections[0].header.add_paragraph("Private header placeholder")
    document.core_properties.author = "Template Author"
    document.save(str(path))


def test_writer_applies_layout_content_links_and_safe_metadata(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "template.docx"
    template(template_path)
    store = ArtefactStore(tmp_path / "artefacts")
    writer = WordWriter(store, available_fonts={"Aptos", "Aptos Display"})

    candidate = writer.write(
        content(),
        "Senior Python Engineer",
        "Acme/cv-generations/generation-1",
        template_path,
        layout(),
    )

    assert candidate.filename == ("Avery Morgan - Senior Python Engineer.docx")
    assert len(candidate.sha256) == 64
    document = Document(str(store.resolve(candidate.relative_path)))
    texts = [paragraph.text for paragraph in document.paragraphs]
    assert texts == [
        "Avery Morgan",
        ("Software Engineer | avery@example.test | https://example.test/avery"),
        "Profile",
        "Builds typed Python services.",
        "Skills",
        "Python, FastAPI",
    ]
    assert "Private placeholder" not in " ".join(texts)
    assert all(
        "Private header" not in paragraph.text
        for paragraph in document.sections[0].header.paragraphs
    )
    section = document.sections[0]
    assert section.page_width == pytest.approx(Mm(210), abs=1_000)
    assert section.page_height == pytest.approx(Mm(297), abs=1_000)
    assert section.left_margin == pytest.approx(Inches(0.55), abs=1_000)
    assert document.styles["Normal"].font.name == "Aptos"
    assert document.styles["Heading 1"].font.name == "Aptos Display"
    bullet_style = document.paragraphs[-1].style
    assert bullet_style is not None
    assert bullet_style.name == "List Bullet"
    relationships = document.part.rels.values()
    targets = {
        relationship.target_ref
        for relationship in relationships
        if relationship.reltype.endswith("/hyperlink")
    }
    assert targets == {
        "mailto:avery@example.test",
        "https://example.test/avery",
    }
    properties = document.core_properties
    assert properties.title == ("Avery Morgan - Senior Python Engineer")
    assert properties.author == "Avery Morgan"
    assert properties.last_modified_by == "JobHunter"


@pytest.mark.parametrize(
    ("name", "role", "expected"),
    [
        (
            "Avery Morgan",
            "Platform/Infrastructure Engineer",
            "Avery Morgan - Platform Infrastructure Engineer.docx",
        ),
        ("../", "../../", "Candidate - Role.docx"),
        ("CON", "Engineer", "_CON - Engineer.docx"),
    ],
)
def test_candidate_filename_is_portable(
    name: str, role: str, expected: str
) -> None:
    assert candidate_filename(name, role) == expected


def test_writer_rejects_missing_styles_fonts_and_unsafe_output(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "template.docx"
    template(template_path)
    document = Document(str(template_path))
    title = document.styles["Title"]
    title.element.getparent().remove(title.element)
    document.save(str(template_path))
    store = ArtefactStore(tmp_path / "artefacts")

    with pytest.raises(WordWriterError, match="missing required style"):
        WordWriter(store).write(
            content(), "Engineer", "safe", template_path, layout()
        )

    template(template_path)
    with pytest.raises(WordWriterError, match="fonts are unavailable"):
        WordWriter(store, available_fonts={"Aptos"}).write(
            content(), "Engineer", "safe", template_path, layout()
        )

    with pytest.raises(UnsafeArtefactPathError):
        WordWriter(store).write(
            content(), "Engineer", "../escape", template_path, layout()
        )
    assert list(store.root.rglob("*.docx")) == []


def test_writer_rejects_corrupt_template(tmp_path: Path) -> None:
    template_path = tmp_path / "template.docx"
    template_path.write_bytes(b"not a DOCX")

    with pytest.raises(WordWriterError, match="corrupt or unreadable"):
        WordWriter(ArtefactStore(tmp_path / "artefacts")).write(
            content(), "Engineer", "safe", template_path, layout()
        )


def test_writer_sets_east_asia_font_names(tmp_path: Path) -> None:
    template_path = tmp_path / "template.docx"
    template(template_path)
    store = ArtefactStore(tmp_path / "artefacts")

    candidate = WordWriter(store).write(
        content(), "Engineer", "safe", template_path, layout()
    )

    document = Document(str(store.resolve(candidate.relative_path)))
    normal_fonts = document.styles["Normal"].element.rPr.rFonts
    assert normal_fonts.get(qn("w:eastAsia")) == "Aptos"


def test_writer_uses_configured_section_order_and_link_color(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "template.docx"
    template(template_path)
    store = ArtefactStore(tmp_path / "artefacts")
    configured = layout().model_copy(
        update={
            "section_order": (
                "skills",
                "summary",
                "experience",
                "projects",
                "education",
                "certifications",
                "additional",
            )
        }
    )

    candidate = WordWriter(store).write(
        content(), "Engineer", "safe", template_path, configured
    )

    document = Document(str(store.resolve(candidate.relative_path)))
    assert [paragraph.text for paragraph in document.paragraphs][2:] == [
        "Skills",
        "Python, FastAPI",
        "Profile",
        "Builds typed Python services.",
    ]
    hyperlink_colors = document._element.xpath(
        ".//w:hyperlink/w:r/w:rPr/w:color/@w:val"
    )
    assert hyperlink_colors == ["2563EB", "2563EB"]
