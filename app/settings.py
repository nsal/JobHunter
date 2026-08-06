"""Typed configuration and validation for private AI workflow inputs."""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from docx import Document
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AI_CONFIG = ROOT / "config" / "ai.yaml"
MAX_CONFIG_BYTES = 64 * 1024
MAX_PROFILE_BYTES = 1024 * 1024
MAX_TEMPLATE_BYTES = 10 * 1024 * 1024
MAX_LAYOUT_BYTES = 64 * 1024


class SettingsError(ValueError):
    """Raised when tracked settings or private inputs are invalid."""


class StrictModel(BaseModel):
    """Base class for immutable settings that reject unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderSettings(StrictModel):
    """Provider and model routing used by structured generation."""

    name: Literal["openai"] = "openai"
    assessment_model: str = Field(min_length=1, max_length=100)
    cv_model: str = Field(min_length=1, max_length=100)
    request_timeout_seconds: float = Field(ge=1, le=300)

    @field_validator("assessment_model", "cv_model")
    @classmethod
    def model_name_must_be_trimmed(cls, value: str) -> str:
        """Reject whitespace-only or padded provider model names."""
        if value != value.strip():
            raise ValueError("model names must not have surrounding whitespace")
        return value


class ScoringSettings(StrictModel):
    """Deterministic match threshold and taxonomy selection."""

    threshold: float = Field(ge=0, le=100)
    taxonomy_version: str = Field(pattern=r"^v[1-9][0-9]*$", max_length=20)


class QueueSettings(StrictModel):
    """Dispatcher polling, liveness, retry, and concurrency bounds."""

    poll_interval_seconds: float = Field(ge=0.1, le=30)
    heartbeat_interval_seconds: float = Field(ge=1, le=120)
    work_lease_seconds: float = Field(ge=5, le=900)
    resource_lease_seconds: float = Field(ge=5, le=900)
    concurrency: int = Field(ge=1, le=3)
    max_attempts: int = Field(default=2, ge=1, le=2)

    @model_validator(mode="after")
    def timings_allow_heartbeats(self) -> QueueSettings:
        """Ensure polling and heartbeat activity occurs before lease expiry."""
        if self.poll_interval_seconds >= self.heartbeat_interval_seconds:
            raise ValueError(
                "poll interval must be shorter than heartbeat interval"
            )
        if self.heartbeat_interval_seconds * 2 >= self.work_lease_seconds:
            raise ValueError("work lease must exceed two heartbeat intervals")
        return self


class AiSettings(StrictModel):
    """Complete tracked AI and dispatcher configuration."""

    provider: ProviderSettings
    scoring: ScoringSettings
    queue: QueueSettings


class MarginSettings(StrictModel):
    """Page margins in inches."""

    top: float = Field(ge=0.25, le=1.5)
    right: float = Field(ge=0.25, le=1.5)
    bottom: float = Field(ge=0.25, le=1.5)
    left: float = Field(ge=0.25, le=1.5)


class PageSettings(StrictModel):
    """Supported document page geometry."""

    size: Literal["A4", "LETTER"]
    orientation: Literal["portrait"] = "portrait"
    margins: MarginSettings


class FontSettings(StrictModel):
    """Bounded document font choices and sizes."""

    body: str = Field(min_length=1, max_length=100)
    headings: str = Field(min_length=1, max_length=100)
    body_size_pt: float = Field(ge=8, le=12)
    heading_size_pt: float = Field(ge=10, le=18)

    @field_validator("body", "headings")
    @classmethod
    def font_name_must_be_trimmed(cls, value: str) -> str:
        """Reject control characters and padded font names."""
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("font names must be trimmed printable text")
        return value


class SpacingSettings(StrictModel):
    """Bounded paragraph and section spacing."""

    line: float = Field(ge=0.8, le=1.5)
    paragraph_after_pt: float = Field(ge=0, le=12)
    section_after_pt: float = Field(ge=0, le=18)


class StyleSettings(StrictModel):
    """Small set of deterministic document style values."""

    heading_color: str = Field(pattern=r"^[0-9A-Fa-f]{6}$")
    accent_color: str = Field(pattern=r"^[0-9A-Fa-f]{6}$")
    bullet_indent_inches: float = Field(ge=0, le=0.5)


class OutputSettings(StrictModel):
    """Safe generated document output name."""

    filename: str = Field(min_length=6, max_length=120)

    @field_validator("filename")
    @classmethod
    def filename_must_be_safe_docx(cls, value: str) -> str:
        """Require a portable basename with the DOCX extension."""
        if (
            value != value.strip()
            or Path(value).name != value
            or not value.lower().endswith(".docx")
            or not re.fullmatch(r"[\w .()-]+", value, flags=re.UNICODE)
        ):
            raise ValueError("filename must be a safe DOCX basename")
        return value


class CvLayoutSettings(StrictModel):
    """Validated private settings used by deterministic DOCX generation."""

    page: PageSettings
    fonts: FontSettings
    spacing: SpacingSettings
    styles: StyleSettings
    output: OutputSettings

    @model_validator(mode="after")
    def hierarchy_must_remain_visible(self) -> CvLayoutSettings:
        """Keep headings larger than body text in the one-page layout."""
        if self.fonts.heading_size_pt <= self.fonts.body_size_pt:
            raise ValueError("heading font must be larger than body font")
        return self


@dataclass(frozen=True)
class PrivateInputs:
    """Validated paths, layout settings, and hashes for private inputs."""

    profile_path: Path
    template_path: Path
    layout_path: Path
    layout: CvLayoutSettings
    profile_sha256: str
    template_sha256: str
    layout_sha256: str


def _secret_like_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    parts = set(normalized.split("_"))
    return (
        bool(
            parts
            & {
                "apikey",
                "authorization",
                "credential",
                "credentials",
                "password",
                "secret",
                "token",
            }
        )
        or "api_key" in normalized
    )


def _reject_secret_keys(value: object, location: str = "settings") -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key)
            if _secret_like_key(key):
                raise SettingsError(
                    f"Secret-like key {location}.{key} is not allowed in YAML."
                )
            _reject_secret_keys(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_secret_keys(nested, f"{location}[{index}]")


def _read_yaml_mapping(path: Path, maximum_bytes: int) -> dict[str, Any]:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise SettingsError(f"Cannot read YAML file: {path}") from error
    if size == 0 or size > maximum_bytes:
        raise SettingsError(
            f"YAML file must be between 1 and {maximum_bytes} bytes: {path}"
        )
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise SettingsError(f"YAML file is invalid: {path}") from error
    if not isinstance(loaded, dict) or not all(
        isinstance(key, str) for key in loaded
    ):
        raise SettingsError(f"YAML root must be a mapping: {path}")
    return cast(dict[str, Any], loaded)


def _merge_settings(
    base: dict[str, Any], overrides: Mapping[str, object]
) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = _merge_settings(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_ai_settings(
    path: str | Path = DEFAULT_AI_CONFIG,
    overrides: Mapping[str, object] | None = None,
) -> AiSettings:
    """Load tracked AI settings, optionally applying typed test overrides."""
    config_path = Path(path)
    values = _read_yaml_mapping(config_path, MAX_CONFIG_BYTES)
    _reject_secret_keys(values)
    if overrides:
        _reject_secret_keys(overrides, "overrides")
        values = _merge_settings(values, overrides)
    try:
        return AiSettings.model_validate(values)
    except ValidationError as error:
        raise SettingsError(f"AI settings are invalid: {error}") from error


def _reject_symlinks(path: Path, boundary: Path) -> None:
    try:
        relative = path.relative_to(boundary)
    except ValueError as error:
        raise SettingsError(
            f"Private input is outside {boundary}: {path}"
        ) from error
    if ".." in relative.parts:
        raise SettingsError(f"Private input escapes {boundary}: {path}")
    current = boundary
    if boundary.is_symlink():
        raise SettingsError(f"Private input boundary is a symlink: {boundary}")
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SettingsError(f"Private input must not use symlinks: {path}")


def _validate_private_file(
    path: Path,
    private_root: Path,
    suffix: str,
    maximum_bytes: int,
) -> None:
    if not path.is_absolute():
        path = private_root.parent / path
    try:
        path.relative_to(private_root)
    except ValueError as error:
        raise SettingsError(
            f"Private input is outside {private_root}: {path}"
        ) from error
    _reject_symlinks(path, private_root)
    if path.suffix.lower() != suffix or not path.is_file():
        raise SettingsError(f"Missing or invalid private {suffix} file: {path}")
    size = path.stat().st_size
    if size == 0 or size > maximum_bytes:
        raise SettingsError(
            f"Private input must be between 1 and {maximum_bytes} bytes: {path}"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_private_inputs(
    project_root: str | Path = ROOT,
    *,
    profile_path: str | Path | None = None,
    template_path: str | Path | None = None,
    layout_path: str | Path | None = None,
) -> PrivateInputs:
    """Validate the active profile, DOCX template, and layout configuration."""
    root = Path(project_root).resolve()
    private_root = root / "private"
    default_root = private_root / "profile"
    profile = Path(profile_path or default_root / "profile.md")
    template = Path(template_path or default_root / "cv-template.docx")
    layout_file = Path(layout_path or default_root / "cv-layout.yaml")
    if not profile.is_absolute():
        profile = root / profile
    if not template.is_absolute():
        template = root / template
    if not layout_file.is_absolute():
        layout_file = root / layout_file

    _validate_private_file(profile, private_root, ".md", MAX_PROFILE_BYTES)
    _validate_private_file(template, private_root, ".docx", MAX_TEMPLATE_BYTES)
    _validate_private_file(layout_file, private_root, ".yaml", MAX_LAYOUT_BYTES)

    try:
        profile_text = profile.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SettingsError(f"Profile is not valid UTF-8: {profile}") from error
    if not profile_text.strip():
        raise SettingsError(
            f"Profile must contain non-whitespace text: {profile}"
        )

    if not zipfile.is_zipfile(template):
        raise SettingsError(f"CV template is not a valid DOCX file: {template}")
    try:
        Document(str(template))
    except Exception as error:
        raise SettingsError(f"CV template is corrupt: {template}") from error

    layout_values = _read_yaml_mapping(layout_file, MAX_LAYOUT_BYTES)
    _reject_secret_keys(layout_values, "layout")
    try:
        parsed_layout = CvLayoutSettings.model_validate(layout_values)
    except ValidationError as error:
        raise SettingsError(f"CV layout is invalid: {error}") from error

    return PrivateInputs(
        profile_path=profile,
        template_path=template,
        layout_path=layout_file,
        layout=parsed_layout,
        profile_sha256=_sha256(profile),
        template_sha256=_sha256(template),
        layout_sha256=_sha256(layout_file),
    )
