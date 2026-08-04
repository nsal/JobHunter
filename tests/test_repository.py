from pathlib import Path

import pytest

from app.database import connect, initialize_database
from app.repository import ApplicationNotFoundError, Repository


def test_create_update_and_lookup(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        {"role": "Developer", "company": "Acme", "is_fully_remote": "on"},
        "2026-01-01T09:00:00",
    )
    application = repository.get_application(application_id)
    assert application["current_stage"] == "Submitted"
    assert application["submitted_date"] == "2026-01-01T09:00:00"
    repository.update_application(
        application_id,
        {"role": "Senior Developer", "company": "Acme", "notes": "Follow up"},
    )
    updated = repository.get_application(application_id)
    assert updated["role"] == "Senior Developer"
    assert updated["last_updated_date"] == "2026-01-01T09:00:00"


def test_stage_transition_is_sequenced_and_validated(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        {"role": "Developer", "company": "Acme"}, "2026-01-01T09:00:00"
    )
    repository.add_stage(
        application_id, "Interview", "2026-01-02T09:00:00", "First round"
    )
    application = repository.get_application(application_id)
    assert application["current_stage"] == "Interview"
    assert [
        (row["stage_sequence"], row["is_current"])
        for row in application["history"]
    ] == [(2, 1), (1, 0)]
    assert application["history"][1]["effective_to"] == "2026-01-02T09:00:00"
    with pytest.raises(ValueError, match="cannot precede"):
        repository.add_stage(application_id, "Offer", "2026-01-01T00:00:00")
    with pytest.raises(ApplicationNotFoundError):
        repository.add_stage(999, "Viewed", "2026-01-02T09:00:00")


def test_list_order_uses_current_stage_date(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    first = repository.create_application(
        {"role": "First", "company": "Acme"}, "2026-01-01T09:00:00"
    )
    second = repository.create_application(
        {"role": "Second", "company": "Beta"}, "2026-01-02T09:00:00"
    )
    repository.update_application(first, {"role": "Changed", "company": "Acme"})
    assert [row["id"] for row in repository.list_applications()] == [
        second,
        first,
    ]


def test_searches_role_company_and_notes(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    role = repository.create_application(
        {"role": "Python Engineer", "company": "Acme"},
        "2026-01-01T09:00:00",
    )
    company = repository.create_application(
        {"role": "Designer", "company": "Pythonic Ltd"},
        "2026-01-02T09:00:00",
    )
    notes = repository.create_application(
        {"role": "Manager", "company": "Beta", "notes": "PYTHON call"},
        "2026-01-03T09:00:00",
    )

    assert {item["id"] for item in repository.list_applications("python")} == {
        role,
        company,
        notes,
    }
    assert repository.list_applications("missing") == []
    assert len(repository.list_applications("   ")) == 3


def test_normalizes_optional_job_url_and_local_cv_path(
    database_path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    cv_path = cv_root / "resume.pdf"
    cv_path.touch()
    application_id = repository.create_application(
        {
            "role": "Developer",
            "company": "Acme",
            "job_url": "https://jobs.example.test/123",
            "cv_path": str(cv_path),
        },
        "2026-01-01T09:00:00",
    )

    application = repository.get_application(application_id)
    assert application["job_url"] == "https://jobs.example.test/123"
    assert application["cv_path"] == cv_path.as_uri()

    repository.update_application(
        application_id,
        {
            "role": "Developer",
            "company": "Acme",
            "job_url": "",
            "cv_path": str(cv_path),
        },
    )
    updated = repository.get_application(application_id)
    assert updated["job_url"] is None
    assert updated["cv_path"] == cv_path.as_uri()


@pytest.mark.parametrize("suffix", [".pdf", ".doc", ".docx"])
def test_accepts_supported_cv_files(
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    cv_path = cv_root / f"resume{suffix}"
    cv_path.touch()

    application_id = repository.create_application(
        {"role": "Developer", "company": "Acme", "cv_path": str(cv_path)},
        "2026-01-01T09:00:00",
    )

    assert (
        repository.get_application(application_id)["cv_path"]
        == cv_path.as_uri()
    )


@pytest.mark.parametrize("name", ["missing.pdf", "resume.txt"])
def test_rejects_missing_or_unsupported_cv_files(
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    candidate = cv_root / name
    if candidate.suffix == ".txt":
        candidate.touch()

    with pytest.raises(ValueError):
        repository.create_application(
            {"role": "Developer", "company": "Acme", "cv_path": str(candidate)},
            "2026-01-01T09:00:00",
        )


def test_rejects_cv_file_outside_configured_root(
    database_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    cv_root = tmp_path / "cvs"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    outside = tmp_path / "resume.pdf"
    outside.touch()

    with pytest.raises(ValueError, match="configured CV root"):
        repository.create_application(
            {"role": "Developer", "company": "Acme", "cv_path": str(outside)},
            "2026-01-01T09:00:00",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("job_url", "javascript:alert(1)"),
        ("job_url", "jobs.example.test/123"),
        ("cv_path", "resume.pdf"),
        ("cv_path", "javascript:alert(1)"),
        ("cv_path", "https://files.example.test/resume.pdf"),
    ],
)
def test_rejects_unsafe_or_relative_link_values(
    database_path: str, field: str, value: str
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    values = {"role": "Developer", "company": "Acme", field: value}

    with pytest.raises(ValueError):
        repository.create_application(values, "2026-01-01T09:00:00")


def test_does_not_render_unsafe_legacy_link_values(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        {"role": "Developer", "company": "Acme"},
        "2026-01-01T09:00:00",
    )
    with connect(database_path) as connection:
        connection.execute(
            """UPDATE applications
            SET job_url = ?, cv_path = ? WHERE id = ?""",
            ("javascript:alert(1)", "resume.pdf", application_id),
        )

    application = repository.get_application(application_id)
    assert application["job_url"] is None
    assert application["cv_path"] is None
    assert repository.list_applications()[0]["job_url"] is None
