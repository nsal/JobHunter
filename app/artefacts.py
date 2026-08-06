"""Safe rooted storage for private application artefacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import unicodedata
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

MAX_SEGMENT_LENGTH = 80
WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}


class UnsafeArtefactPathError(ValueError):
    """Raised when a requested artefact path is unsafe."""


def _replace_path(source: Path, target: Path) -> None:
    os.replace(source, target)


def safe_segment(value: str, fallback: str = "item") -> str:
    """Return a portable, bounded path segment while retaining Unicode text."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = re.sub(r"\s+", "-", normalized)
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in normalized
    )
    cleaned = re.sub(r"[-_]{2,}", "-", cleaned).strip("-_. ")
    if not cleaned:
        cleaned = fallback
    cleaned = cleaned[:MAX_SEGMENT_LENGTH].rstrip("-_. ") or fallback
    if cleaned.casefold() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _is_safe_path_segment(value: str) -> bool:
    if (
        not value
        or len(value) > MAX_SEGMENT_LENGTH
        or value != unicodedata.normalize("NFKC", value)
        or value != value.strip()
        or value.endswith((".", " "))
    ):
        return False
    if not all(
        character.isalnum() or character in {"-", "_", ".", " ", "(", ")"}
        for character in value
    ):
        return False
    return (
        value.split(".", maxsplit=1)[0].casefold() not in WINDOWS_RESERVED_NAMES
    )


def application_directory_base(
    company: str, role: str, created_at: str
) -> Path:
    """Build the canonical relative directory for an application."""
    date = created_at[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("Created date must begin with YYYY-MM-DD.")
    return Path(safe_segment(company, "company")) / safe_segment(
        f"{date}_{role}", f"{date}_role"
    )


def _portable_path_key(value: str) -> str:
    """Return a normalized key for case-insensitive filesystem comparison."""
    return unicodedata.normalize("NFKC", value).casefold()


def allocate_application_directory(
    company: str,
    role: str,
    created_at: str,
    application_id: int,
    existing: Collection[str],
) -> str:
    """Allocate a stable relative directory, appending the ID on collision."""
    base = application_directory_base(company, role, created_at)
    candidate = base.as_posix()
    existing_keys = {_portable_path_key(path) for path in existing}
    if _portable_path_key(candidate) in existing_keys:
        suffix = f"_{application_id}"
        stem = base.name[: MAX_SEGMENT_LENGTH - len(suffix)].rstrip("-_. ")
        candidate = base.with_name(f"{stem}{suffix}").as_posix()
    if _portable_path_key(candidate) in existing_keys:
        raise ValueError("Application artefact directory collision.")
    return candidate


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest for bytes."""
    return hashlib.sha256(value).hexdigest()


class ArtefactStore:
    """Read and atomically write files beneath one private artefact root."""

    def __init__(
        self,
        root: str | Path,
        *,
        replace: Callable[[Path, Path], None] = _replace_path,
    ) -> None:
        self.root = Path(root).absolute()
        self._replace = replace
        if self.root.is_symlink():
            raise UnsafeArtefactPathError(
                f"Artefact root must not be a symlink: {self.root}"
            )
        self.root.mkdir(parents=True, exist_ok=True)

    def _relative_parts(self, relative_path: str | Path) -> tuple[str, ...]:
        path = Path(relative_path)
        if path.is_absolute() or not path.parts:
            raise UnsafeArtefactPathError("Artefact path must be relative.")
        if any(part in {"", ".", ".."} for part in path.parts):
            raise UnsafeArtefactPathError(
                f"Artefact path contains traversal: {relative_path}"
            )
        if any(not _is_safe_path_segment(part) for part in path.parts):
            raise UnsafeArtefactPathError(
                f"Artefact path contains an unsafe segment: {relative_path}"
            )
        return path.parts

    def resolve(
        self, relative_path: str | Path, *, must_exist: bool = False
    ) -> Path:
        """Resolve a validated relative path and reject any symlink component."""
        parts = self._relative_parts(relative_path)
        candidate = self.root.joinpath(*parts)
        current = self.root
        for part in parts:
            current /= part
            if current.is_symlink():
                raise UnsafeArtefactPathError(
                    f"Artefact paths must not use symlinks: {relative_path}"
                )
        if not candidate.resolve(strict=False).is_relative_to(
            self.root.resolve(strict=True)
        ):
            raise UnsafeArtefactPathError(
                f"Artefact path escapes its root: {relative_path}"
            )
        if must_exist and not candidate.is_file():
            raise FileNotFoundError(candidate)
        return candidate

    def _make_parent(self, target: Path) -> None:
        current = self.root
        for part in target.relative_to(self.root).parent.parts:
            current /= part
            if current.is_symlink():
                raise UnsafeArtefactPathError(
                    f"Artefact directory must not be a symlink: {current}"
                )
            current.mkdir(exist_ok=True)
            if not current.is_dir() or current.is_symlink():
                raise UnsafeArtefactPathError(
                    f"Artefact parent is not a safe directory: {current}"
                )

    def write_bytes(self, relative_path: str | Path, value: bytes) -> Path:
        """Atomically replace an artefact with the supplied bytes."""
        target = self.resolve(relative_path)
        self._make_parent(target)
        temporary = target.with_name(
            f"{target.name}.tmp-{secrets.token_hex(8)}"
        )
        try:
            with temporary.open("xb") as output:
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            self._replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def write_text(self, relative_path: str | Path, value: str) -> Path:
        """Atomically write UTF-8 text."""
        return self.write_bytes(relative_path, value.encode("utf-8"))

    def write_json(self, relative_path: str | Path, value: Any) -> Path:
        """Atomically write deterministic indented JSON."""
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        return self.write_text(relative_path, f"{serialized}\n")

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """Read bytes from a validated existing artefact."""
        return self.resolve(relative_path, must_exist=True).read_bytes()

    def read_text(self, relative_path: str | Path) -> str:
        """Read UTF-8 text from a validated existing artefact."""
        return self.resolve(relative_path, must_exist=True).read_text(
            encoding="utf-8"
        )

    def read_json(self, relative_path: str | Path) -> Any:
        """Read JSON from a validated existing artefact."""
        return json.loads(self.read_text(relative_path))

    def sha256(self, relative_path: str | Path) -> str:
        """Hash a validated existing artefact without exposing its path."""
        digest = hashlib.sha256()
        with self.resolve(relative_path, must_exist=True).open("rb") as source:
            for chunk in iter(lambda: source.read(128 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
