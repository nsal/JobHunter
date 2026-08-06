from typing import Any

import pytest

from app.database import initialize_database
from app.repository import ApplicationNotFoundError, Repository


def application_values(**overrides: Any) -> dict[str, Any]:
    values = {
        "role": "Developer",
        "company": "Acme",
        "full_jd": "Build and maintain software.",
    }
    values.update(overrides)
    return values


def test_create_starts_assessing_without_a_submission_date(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)

    application_id = repository.create_application(
        application_values(is_fully_remote="on"),
        "2026-01-01T09:00:00",
    )
    application = repository.get_application(application_id)

    assert application["current_stage"] == "Assessing"
    assert application["submitted_date"] is None
    assert application["created_at"] == "2026-01-01T09:00:00"
    assert application["full_jd"] == "Build and maintain software."
    assert application["is_fully_remote"] == 1
    assert application["history"][0]["stage_sequence"] == 1


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"company": "Acme", "full_jd": "JD"}, "Role is required"),
        ({"role": "Dev", "full_jd": "JD"}, "Company is required"),
        (
            {"role": "Dev", "company": "Acme"},
            "Full job description is required",
        ),
    ],
)
def test_create_requires_application_inputs(
    database_path: str, values: dict[str, str], message: str
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)

    with pytest.raises(ValueError, match=message):
        repository.create_application(values, "2026-01-01T09:00:00")


def test_create_requires_a_created_date(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)

    with pytest.raises(ValueError, match="Created date is required"):
        repository.create_application(application_values(), "")


def test_update_preserves_jd_created_date_and_history(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(), "2026-01-01T09:00:00"
    )

    repository.update_application(
        application_id,
        application_values(
            role="Senior Developer",
            payment="100k",
            notes="Follow up",
            full_jd="Attempted replacement",
        ),
    )
    updated = repository.get_application(application_id)

    assert updated["role"] == "Senior Developer"
    assert updated["payment"] == "100k"
    assert updated["notes"] == "Follow up"
    assert updated["full_jd"] == "Build and maintain software."
    assert updated["created_at"] == "2026-01-01T09:00:00"
    assert updated["last_updated_date"] == "2026-01-01T09:00:00"
    assert len(updated["history"]) == 1


def test_explicit_submission_sets_the_first_submission_date(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(), "2026-01-01T09:00:00"
    )

    repository.add_stage(
        application_id,
        "Ready to apply",
        "2026-01-02T09:00:00",
    )
    repository.add_stage(
        application_id,
        "Submitted",
        "2026-01-03T09:00:00",
        "Applied directly",
    )
    repository.add_stage(
        application_id,
        "Viewed",
        "2026-01-04T09:00:00",
    )
    repository.add_stage(
        application_id,
        "Submitted",
        "2026-01-05T09:00:00",
    )
    application = repository.get_application(application_id)

    assert application["current_stage"] == "Submitted"
    assert application["submitted_date"] == "2026-01-03T09:00:00"
    assert [item["stage_sequence"] for item in application["history"]] == [
        5,
        4,
        3,
        2,
        1,
    ]
    assert application["history"][1]["effective_to"] == ("2026-01-05T09:00:00")


def test_stage_transition_is_validated_and_atomic(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(), "2026-01-01T09:00:00"
    )
    initial_history = repository.get_application(application_id)["history"]

    with pytest.raises(ValueError, match="Choose a new stage"):
        repository.add_stage(application_id, "", "2026-01-02T09:00:00")
    with pytest.raises(ValueError, match="valid stage"):
        repository.add_stage(application_id, "Invalid", "2026-01-02T09:00:00")
    with pytest.raises(ValueError, match="cannot precede"):
        repository.add_stage(application_id, "Submitted", "2025-01-01T09:00:00")

    assert repository.get_application(application_id)["history"] == (
        initial_history
    )
    with pytest.raises(ApplicationNotFoundError):
        repository.add_stage(999, "Submitted", "2026-01-02T09:00:00")


def test_current_stage_editor_updates_note_or_appends_history(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(), "2026-01-01T09:00:00"
    )

    repository.update_current_stage(
        application_id,
        "Assessing",
        "Assessment queued",
        "2026-01-02T09:00:00",
    )
    note_updated = repository.get_application(application_id)
    assert note_updated["current_stage_description"] == "Assessment queued"
    assert len(note_updated["history"]) == 1

    repository.update_current_stage(
        application_id,
        "Mismatch",
        "Mandatory gap",
        "2026-01-02T09:00:00",
    )
    transitioned = repository.get_application(application_id)
    assert transitioned["current_stage"] == "Mismatch"
    assert transitioned["current_stage_description"] == "Mandatory gap"
    assert len(transitioned["history"]) == 2
    with pytest.raises(ApplicationNotFoundError):
        repository.update_current_stage(
            999, "Viewed", "", "2026-01-03T09:00:00"
        )


def test_notes_editor_replaces_or_clears_notes(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(notes="Old"), "2026-01-01T09:00:00"
    )

    repository.update_notes(application_id, "New\nnotes")
    assert repository.get_application(application_id)["notes"] == "New\nnotes"
    repository.update_notes(application_id, "  ")
    assert repository.get_application(application_id)["notes"] is None
    with pytest.raises(ApplicationNotFoundError):
        repository.update_notes(999, "Missing")


def test_list_order_search_and_nullable_submission(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    first = repository.create_application(
        application_values(role="Python Engineer"),
        "2026-01-01T09:00:00",
    )
    second = repository.create_application(
        application_values(role="Designer", company="Pythonic Ltd"),
        "2026-01-02T09:00:00",
    )
    third = repository.create_application(
        application_values(role="Manager", notes="PYTHON call"),
        "2026-01-03T09:00:00",
    )

    applications = repository.list_applications()
    assert [row["id"] for row in applications] == [third, second, first]
    assert all(row["submitted_date"] is None for row in applications)
    assert {item["id"] for item in repository.list_applications("python")} == {
        first,
        second,
        third,
    }
    assert repository.list_applications("missing") == []


def test_job_url_is_normalized_and_validated(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(job_url="https://jobs.example.test/123"),
        "2026-01-01T09:00:00",
    )

    assert repository.get_application(application_id)["job_url"] == (
        "https://jobs.example.test/123"
    )
    repository.update_application(
        application_id, application_values(job_url="")
    )
    assert repository.get_application(application_id)["job_url"] is None
    with pytest.raises(ValueError, match="absolute HTTP"):
        repository.update_application(
            application_id,
            application_values(job_url="javascript:alert(1)"),
        )


def test_missing_application_operations_raise(database_path: str) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)

    with pytest.raises(ApplicationNotFoundError):
        repository.get_application(999)
    with pytest.raises(ApplicationNotFoundError):
        repository.update_application(999, application_values())
