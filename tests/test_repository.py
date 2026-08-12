from typing import Any

import pytest

from app.consent import ConsentRepository, ConsentRequiredError
from app.database import connect, initialize_database
from app.repository import ApplicationNotFoundError, Repository


def application_values(**overrides: Any) -> dict[str, Any]:
    values = {
        "role": "Developer",
        "company": "Acme",
        "full_jd": "Build and maintain software.",
    }
    values.update(overrides)
    return values


def seed_completed_generation(
    database_path: str, application_id: int, completed_at: str
) -> None:
    """Seed the minimum durable records needed for promotion tests."""
    with connect(database_path) as connection:
        connection.execute(
            """INSERT INTO assessments(
                id, application_id, outcome, final_score,
                supporting_alignment, mandatory_coverage, threshold,
                all_mandatory_matched, failed_hard_gates,
                ambiguous_hard_gates, model, model_sha256, schema_version,
                schema_sha256, instruction_sha256, taxonomy_version,
                taxonomy_sha256, profile_sha256, jd_sha256, provider,
                response_ids, input_tokens, output_tokens, total_tokens,
                repair_attempted, result_path, result_sha256, analysis_path,
                analysis_sha256, completed_at
            ) VALUES ('assessment-1', ?, 'matched', 90, 90, 90, 80, 1,
                      '[]', '[]', 'model', ?, 'v1', ?, ?, 'taxonomy', ?,
                      ?, ?, 'test', '[]', 1, 2, 3, 0, 'result.json', ?,
                      'analysis.json', ?, '2026-01-01T07:30:00+00:00')""",
            (
                application_id,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "1" * 64,
                "2" * 64,
            ),
        )
        connection.execute(
            """INSERT INTO cv_generations (
                id, application_id, assessment_id, model, model_sha256,
                schema_version, schema_sha256, instruction_sha256,
                profile_sha256, jd_sha256, assessment_result_sha256,
                template_sha256, layout_sha256, provider, response_ids,
                input_tokens, output_tokens, total_tokens, repair_attempted,
                content_path, content_sha256, candidate_path,
                candidate_sha256, completed_at
            ) VALUES ('generation-1', ?, 'assessment-1', 'model', ?, 'v1',
                      ?, ?, ?, ?, ?, ?, ?, 'test', '[]', 1, 2, 3, 0,
                      'content.json', ?, 'candidate.docx', ?, ?)""",
            (
                application_id,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                "e" * 64,
                "f" * 64,
                "1" * 64,
                "2" * 64,
                "3" * 64,
                "4" * 64,
                completed_at,
            ),
        )


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
    assert application["artefact_directory"] == ("Acme/2026-01-01_Developer")
    assert application["full_jd"] == "Build and maintain software."
    assert application["is_fully_remote"] == 1
    assert application["history"][0]["stage_sequence"] == 1


@pytest.mark.parametrize("state", ["absent", "revoked"])
def test_create_requiring_consent_rolls_back_without_active_consent(
    database_path: str, state: str
) -> None:
    initialize_database(database_path)
    if state == "revoked":
        consent = ConsentRepository(database_path)
        consent.acknowledge_openai_profile_sharing("2026-01-01T00:00:00Z")
        consent.revoke_openai_profile_sharing("2026-01-01T01:00:00Z")

    repository = Repository(database_path)

    with pytest.raises(ConsentRequiredError):
        repository.create_application(
            application_values(),
            "2026-01-01T09:00:00",
            require_profile_consent=True,
        )

    with connect(database_path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM applications").fetchone()[
                0
            ]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM application_stage_history"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
            == 0
        )


def test_create_requiring_consent_commits_application_and_work(
    database_path: str,
) -> None:
    initialize_database(database_path)
    ConsentRepository(database_path).acknowledge_openai_profile_sharing(
        "2026-01-01T00:00:00Z"
    )

    application_id = Repository(database_path).create_application(
        application_values(),
        "2026-01-01T09:00:00",
        require_profile_consent=True,
    )

    with connect(database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM application_stage_history WHERE application_id = ?",
                (application_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM work_items WHERE application_id = ?",
                (application_id,),
            ).fetchone()[0]
            == 1
        )


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
    assert updated["artefact_directory"] == "Acme/2026-01-01_Developer"
    assert updated["last_updated_date"] == "2026-01-01T09:00:00"
    assert len(updated["history"]) == 1


def test_artefact_directory_handles_collisions_and_stays_stable(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    first = repository.create_application(
        application_values(company="Société Générale", role="CON"),
        "2026-01-01T09:00:00",
    )
    second = repository.create_application(
        application_values(company="Société Générale", role="CON"),
        "2026-01-01T10:00:00",
    )

    first_directory = repository.get_application(first)["artefact_directory"]
    second_directory = repository.get_application(second)["artefact_directory"]
    assert first_directory == "Société-Générale/2026-01-01_CON"
    assert second_directory == f"{first_directory}_{second}"

    repository.update_application(
        first,
        application_values(company="Renamed", role="Principal Engineer"),
    )
    assert repository.get_application(first)["artefact_directory"] == (
        first_directory
    )


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
        "Ready for review",
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


def test_generation_promotion_canonicalizes_offset_stage_timestamps(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(), "2026-01-01T12:00:00+05:00"
    )
    seed_completed_generation(
        database_path, application_id, "2026-01-01T08:00:00+00:00"
    )

    assert repository.promote_completed_generation(application_id) is True

    application = repository.get_application(application_id)
    closed_stage = application["history"][1]
    assert closed_stage["effective_from"] == (
        "2026-01-01T07:00:00.000000+00:00"
    )
    assert closed_stage["effective_to"] == "2026-01-01T08:00:00.000000+00:00"
    assert closed_stage["effective_to"] >= closed_stage["effective_from"]
    assert application["history"][0]["stage"] == "Ready for review"
    assert repository.promote_completed_generation(application_id) is False
    assert len(repository.get_application(application_id)["history"]) == 2


def test_older_generation_does_not_change_offset_stage_history(
    database_path: str,
) -> None:
    initialize_database(database_path)
    repository = Repository(database_path)
    application_id = repository.create_application(
        application_values(), "2026-01-01T12:00:00+05:00"
    )
    seed_completed_generation(
        database_path, application_id, "2026-01-01T06:00:00+00:00"
    )
    before = repository.get_application(application_id)["history"]

    assert repository.promote_completed_generation(application_id) is False

    assert repository.get_application(application_id)["history"] == before
