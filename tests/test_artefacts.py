from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.artefacts import (
    ArtefactStore,
    UnsafeArtefactPathError,
    allocate_application_directory,
    application_directory_base,
    safe_segment,
    sha256_bytes,
)


def test_safe_segments_cover_unicode_reserved_names_and_punctuation() -> None:
    assert safe_segment("  Société Générale  ") == "Société-Générale"
    assert safe_segment("CON") == "_CON"
    assert safe_segment("../Senior: Engineer") == "Senior-Engineer"
    assert safe_segment("***", "company") == "company"


def test_application_directory_is_stable_and_appends_id_on_collision() -> None:
    base = application_directory_base(
        "Société Générale", "Senior Engineer", "2026-08-06T12:00:00"
    )
    ordinary = allocate_application_directory(
        "Société Générale",
        "Senior Engineer",
        "2026-08-06T12:00:00",
        7,
        set(),
    )
    collision = allocate_application_directory(
        "Société Générale",
        "Senior Engineer",
        "2026-08-06T12:00:00",
        7,
        {ordinary},
    )
    case_collision = allocate_application_directory(
        "société générale",
        "senior engineer",
        "2026-08-06T12:00:00",
        8,
        {ordinary},
    )

    assert ordinary == base.as_posix()
    assert collision == f"{base.parent.as_posix()}/{base.name}_7"
    assert case_collision == ("société-générale/2026-08-06_senior-engineer_8")


def test_artefact_store_round_trips_json_text_binary_and_hashes(
    tmp_path: Path,
) -> None:
    store = ArtefactStore(tmp_path / "artefacts")
    payload = b"\x00private bytes\xff"

    store.write_text("Acme/2026-08-06_Engineer/analysis.txt", "résumé")
    store.write_json(
        "Acme/2026-08-06_Engineer/assessment-result.json",
        {"score": 80, "matched": True},
    )
    store.write_bytes("Acme/2026-08-06_Engineer/candidate.docx", payload)

    assert store.read_text("Acme/2026-08-06_Engineer/analysis.txt") == "résumé"
    assert store.read_json(
        "Acme/2026-08-06_Engineer/assessment-result.json"
    ) == {"matched": True, "score": 80}
    assert (
        store.read_bytes("Acme/2026-08-06_Engineer/candidate.docx") == payload
    )
    assert (
        store.sha256("Acme/2026-08-06_Engineer/candidate.docx")
        == hashlib.sha256(payload).hexdigest()
    )
    assert sha256_bytes(payload) == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    "unsafe",
    [
        "../escape.json",
        "/absolute.json",
        "safe/../../escape.json",
        "safe/bad:name.json",
        "safe/CON.json",
        "safe/trailing. ",
    ],
)
def test_artefact_store_rejects_unsafe_paths(
    tmp_path: Path, unsafe: str
) -> None:
    store = ArtefactStore(tmp_path / "artefacts")

    with pytest.raises(UnsafeArtefactPathError):
        store.write_text(unsafe, "unsafe")


def test_artefact_store_rejects_symlinked_root_parent_and_file(
    tmp_path: Path,
) -> None:
    store = ArtefactStore(tmp_path / "artefacts")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeArtefactPathError, match="symlinks"):
        store.write_text("linked/escape.txt", "unsafe")

    safe_parent = store.root / "safe"
    safe_parent.mkdir()
    outside_file = outside / "outside.txt"
    outside_file.write_text("private", encoding="utf-8")
    (safe_parent / "linked.txt").symlink_to(outside_file)
    with pytest.raises(UnsafeArtefactPathError, match="symlinks"):
        store.read_text("safe/linked.txt")

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafeArtefactPathError, match="root"):
        ArtefactStore(linked_root)


def test_atomic_write_failure_preserves_original_and_cleans_temporary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "artefacts"
    ordinary_store = ArtefactStore(root)
    ordinary_store.write_text("Acme/result.json", "original")

    def fail_replace(
        source: os.PathLike[str], target: os.PathLike[str]
    ) -> None:
        del source, target
        raise OSError("simulated replacement failure")

    failing_store = ArtefactStore(root, replace=fail_replace)
    with pytest.raises(OSError, match="simulated"):
        failing_store.write_text("Acme/result.json", "replacement")

    assert ordinary_store.read_text("Acme/result.json") == "original"
    assert list((root / "Acme").glob("*.tmp-*")) == []
