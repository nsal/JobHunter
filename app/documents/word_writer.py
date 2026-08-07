"""Deterministically render validated CV content into a DOCX template."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from docx import Document
from docx.document import Document as DocumentObject
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor
from docx.text.paragraph import Paragraph

from app.ai.schema_models import CvContent, CvSectionKind
from app.artefacts import MAX_SEGMENT_LENGTH, ArtefactStore
from app.settings import CvLayoutSettings

REQUIRED_PARAGRAPH_STYLES = ("Normal", "Title", "Heading 1", "List Bullet")
WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class WordWriterError(ValueError):
    """Raised when a DOCX cannot be rendered safely and deterministically."""


@dataclass(frozen=True)
class CandidateDocument:
    """The persisted DOCX candidate and its content hash."""

    filename: str
    relative_path: str
    sha256: str


def _safe_filename_part(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    cleaned = "".join(
        character
        if character.isalnum() or character in {"-", "_", ".", " ", "(", ")"}
        else " "
        for character in normalized
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", maxsplit=1)[0].casefold() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def candidate_filename(full_name: str, role: str) -> str:
    """Build the required portable candidate filename."""
    name = _safe_filename_part(full_name, "Candidate")
    target_role = _safe_filename_part(role, "Role")
    suffix = ".docx"
    separator = " - "
    maximum_body = MAX_SEGMENT_LENGTH - len(suffix)
    maximum_role = maximum_body - len(name) - len(separator)
    if maximum_role < 1:
        name = name[: maximum_body - len(separator) - 1].rstrip(" .")
        maximum_role = maximum_body - len(name) - len(separator)
    target_role = target_role[:maximum_role].rstrip(" .") or "R"
    return f"{name}{separator}{target_role}{suffix}"


def _set_style_font(style: Any, name: str, size: float) -> None:
    font = style.font
    font.name = name
    font.size = Pt(size)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _validate_styles(document: DocumentObject) -> None:
    for style_name in REQUIRED_PARAGRAPH_STYLES:
        try:
            style = document.styles[style_name]
        except KeyError as error:
            raise WordWriterError(
                f"CV template is missing required style: {style_name}."
            ) from error
        if style.type is not WD_STYLE_TYPE.PARAGRAPH:
            raise WordWriterError(
                f"CV template style must be a paragraph style: {style_name}."
            )


def _clear_story(paragraphs: list[Paragraph]) -> None:
    for paragraph in paragraphs:
        paragraph._element.getparent().remove(paragraph._element)


def _clear_template(document: DocumentObject) -> None:
    body = document._element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)
    for section in document.sections:
        _clear_story(section.header.paragraphs)
        _clear_story(section.footer.paragraphs)


def _apply_page_layout(
    document: DocumentObject, layout: CvLayoutSettings
) -> None:
    for section in document.sections:
        if layout.page.size == "A4":
            section.page_width = Mm(210)
            section.page_height = Mm(297)
        else:
            section.page_width = Inches(8.5)
            section.page_height = Inches(11)
        section.top_margin = Inches(layout.page.margins.top)
        section.right_margin = Inches(layout.page.margins.right)
        section.bottom_margin = Inches(layout.page.margins.bottom)
        section.left_margin = Inches(layout.page.margins.left)


def _apply_styles(document: DocumentObject, layout: CvLayoutSettings) -> None:
    normal = document.styles["Normal"]
    title = document.styles["Title"]
    heading = document.styles["Heading 1"]
    bullet = document.styles["List Bullet"]
    _set_style_font(normal, layout.fonts.body, layout.fonts.body_size_pt)
    _set_style_font(bullet, layout.fonts.body, layout.fonts.body_size_pt)
    _set_style_font(
        heading, layout.fonts.headings, layout.fonts.heading_size_pt
    )
    _set_style_font(
        title, layout.fonts.headings, layout.fonts.heading_size_pt + 4
    )
    heading.font.color.rgb = RGBColor.from_string(
        layout.styles.heading_color.upper()
    )
    title.font.color.rgb = RGBColor.from_string(
        layout.styles.accent_color.upper()
    )
    for style in (normal, bullet):
        paragraph_format = style.paragraph_format
        paragraph_format.line_spacing = layout.spacing.line
        paragraph_format.space_after = Pt(layout.spacing.paragraph_after_pt)
    bullet.paragraph_format.left_indent = Inches(
        layout.styles.bullet_indent_inches
    )
    heading.paragraph_format.space_after = Pt(layout.spacing.section_after_pt)
    heading.paragraph_format.keep_with_next = True


def _hyperlink_target(text: str, kind: str) -> str | None:
    if kind == "email":
        return f"mailto:{text}" if "@" in text else None
    if kind == "phone":
        compact = re.sub(r"[^0-9+]", "", text)
        return f"tel:{compact}" if compact else None
    if kind == "website":
        parsed = urlsplit(text)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            return text
    return None


def _add_hyperlink(
    paragraph: Paragraph, text: str, target: str, color_value: str
) -> None:
    relationship_id = paragraph.part.relate_to(
        target,
        "http://schemas.openxmlformats.org/officeDocument/2006/"
        "relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship_id)
    run = OxmlElement("w:r")
    properties = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), color_value.upper())
    properties.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    properties.append(underline)
    run.append(properties)
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def _add_contact_line(
    document: DocumentObject,
    content: CvContent,
    layout: CvLayoutSettings,
) -> None:
    identity = content.identity
    values = (
        ("professional_title", identity.professional_title),
        ("email", identity.email),
        ("phone", identity.phone),
        ("location", identity.location),
        ("website", identity.website),
    )
    present = tuple(
        (kind, value) for kind, value in values if value is not None
    )
    if not present:
        return
    paragraph = document.add_paragraph(style="Normal")
    paragraph.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    for index, (kind, value) in enumerate(present):
        if index:
            paragraph.add_run(" | ")
        target = _hyperlink_target(value.text, kind)
        if target is None:
            paragraph.add_run(value.text)
        else:
            _add_hyperlink(
                paragraph,
                value.text,
                target,
                layout.styles.accent_color,
            )


def _add_sections(
    document: DocumentObject,
    content: CvContent,
    layout: CvLayoutSettings,
) -> None:
    order = {kind: index for index, kind in enumerate(layout.section_order)}
    sections = sorted(
        content.sections, key=lambda section: order[section.kind.value]
    )
    for section in sections:
        document.add_paragraph(section.heading, style="Heading 1")
        claim_style = (
            "Normal" if section.kind is CvSectionKind.SUMMARY else "List Bullet"
        )
        for claim in section.claims:
            document.add_paragraph(claim.text, style=claim_style)


def _set_safe_metadata(
    document: DocumentObject, content: CvContent, role: str
) -> None:
    properties = document.core_properties
    properties.title = f"{content.identity.full_name.text} - {role}"
    properties.subject = "Tailored curriculum vitae"
    properties.author = content.identity.full_name.text
    properties.last_modified_by = "JobHunter"
    properties.comments = "Generated by JobHunter from validated CV content."
    properties.category = "CV"
    properties.keywords = ""
    properties.identifier = ""
    properties.language = "en"
    properties.version = "1"
    properties.revision = 1
    fixed_time = datetime(2000, 1, 1, tzinfo=UTC)
    properties.created = fixed_time
    properties.modified = fixed_time
    properties.last_printed = fixed_time


class WordWriter:
    """Render CV content into an atomic private DOCX candidate."""

    def __init__(
        self,
        artefacts: ArtefactStore,
        *,
        available_fonts: Collection[str] | None = None,
    ) -> None:
        self._artefacts = artefacts
        self._available_fonts = (
            {font.casefold() for font in available_fonts}
            if available_fonts is not None
            else None
        )

    def _validate_fonts(self, layout: CvLayoutSettings) -> None:
        if self._available_fonts is None:
            return
        missing = sorted(
            font
            for font in (layout.fonts.body, layout.fonts.headings)
            if font.casefold() not in self._available_fonts
        )
        if missing:
            raise WordWriterError(
                "Configured CV fonts are unavailable: " + ", ".join(missing)
            )

    def write(
        self,
        content: CvContent,
        role: str,
        base_path: str | Path,
        template_path: str | Path,
        layout: CvLayoutSettings,
    ) -> CandidateDocument:
        """Write one candidate atomically beneath the generation directory."""
        if not role or role != role.strip():
            raise WordWriterError("Application role must be trimmed text.")
        self._validate_fonts(layout)
        try:
            document = Document(str(template_path))
        except Exception as error:
            raise WordWriterError(
                "CV template is corrupt or unreadable."
            ) from error
        _validate_styles(document)
        _clear_template(document)
        _apply_page_layout(document, layout)
        _apply_styles(document, layout)

        document.add_paragraph(
            content.identity.full_name.text, style="Title"
        ).alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        _add_contact_line(document, content, layout)
        _add_sections(document, content, layout)
        _set_safe_metadata(document, content, role)

        output = BytesIO()
        try:
            document.save(output)
        except Exception as error:
            raise WordWriterError(
                "CV candidate could not be rendered."
            ) from error
        filename = candidate_filename(content.identity.full_name.text, role)
        relative_path = (Path(base_path) / filename).as_posix()
        self._artefacts.resolve(relative_path)
        self._artefacts.write_bytes(relative_path, output.getvalue())
        return CandidateDocument(
            filename=filename,
            relative_path=relative_path,
            sha256=self._artefacts.sha256(relative_path),
        )
