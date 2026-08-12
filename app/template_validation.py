"""Validation shared by CV setup and DOCX generation."""

from __future__ import annotations

from docx.document import Document as DocumentObject
from docx.enum.style import WD_STYLE_TYPE

REQUIRED_PARAGRAPH_STYLES = ("Normal", "Title", "Heading 1", "List Bullet")


class TemplateStyleError(ValueError):
    """Raised when a DOCX template lacks a required paragraph style."""


def validate_template_styles(document: DocumentObject) -> None:
    """Validate styles required by the CV writer."""
    for style_name in REQUIRED_PARAGRAPH_STYLES:
        try:
            style = document.styles[style_name]
        except KeyError as error:
            raise TemplateStyleError(
                f"CV template is missing required style: {style_name}."
            ) from error
        if style.type is not WD_STYLE_TYPE.PARAGRAPH:
            raise TemplateStyleError(
                f"CV template style must be a paragraph style: {style_name}."
            )
