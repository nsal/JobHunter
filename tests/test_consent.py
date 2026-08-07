from __future__ import annotations

import sqlite3

import pytest

from app.consent import ConsentRepository, ConsentRequiredError
from app.database import connect, initialize_database


@pytest.fixture
def consent_repository(database_path: str) -> ConsentRepository:
    initialize_database(database_path)
    return ConsentRepository(database_path)


def test_profile_sharing_consent_is_absent_by_default(
    consent_repository: ConsentRepository,
) -> None:
    state = consent_repository.get_openai_profile_sharing()

    assert state.is_granted is False
    assert state.granted_at is None
    assert state.revoked_at is None
    with pytest.raises(ConsentRequiredError, match="acknowledgement"):
        consent_repository.require_openai_profile_sharing()


def test_profile_sharing_consent_can_be_granted_and_required(
    consent_repository: ConsentRepository,
) -> None:
    state = consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T09:00:00+00:00"
    )

    assert state.is_granted is True
    assert state.granted_at == "2026-08-07T09:00:00+00:00"
    assert state.revoked_at is None
    consent_repository.require_openai_profile_sharing()


def test_repeated_acknowledgement_preserves_the_original_timestamp(
    consent_repository: ConsentRepository,
) -> None:
    first = consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T09:00:00+00:00"
    )
    second = consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T10:00:00+00:00"
    )

    assert second == first


def test_profile_sharing_consent_can_be_revoked_idempotently(
    consent_repository: ConsentRepository,
) -> None:
    consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T09:00:00+00:00"
    )
    first = consent_repository.revoke_openai_profile_sharing(
        "2026-08-07T10:00:00+00:00"
    )
    second = consent_repository.revoke_openai_profile_sharing(
        "2026-08-07T11:00:00+00:00"
    )

    assert first.is_granted is False
    assert second == first
    with pytest.raises(ConsentRequiredError):
        consent_repository.require_openai_profile_sharing()


def test_revoked_consent_can_be_acknowledged_again(
    consent_repository: ConsentRepository,
) -> None:
    consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T09:00:00+00:00"
    )
    consent_repository.revoke_openai_profile_sharing(
        "2026-08-07T10:00:00+00:00"
    )

    state = consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T11:00:00+00:00"
    )

    assert state.is_granted is True
    assert state.granted_at == "2026-08-07T11:00:00+00:00"
    assert state.revoked_at is None


def test_revocation_before_acknowledgement_is_rejected(
    consent_repository: ConsentRepository,
) -> None:
    consent_repository.acknowledge_openai_profile_sharing(
        "2026-08-07T10:00:00+00:00"
    )

    with pytest.raises(ValueError, match="cannot precede"):
        consent_repository.revoke_openai_profile_sharing(
            "2026-08-07T09:00:00+00:00"
        )


def test_consent_database_constraints_reject_blank_values(
    database_path: str,
) -> None:
    initialize_database(database_path)

    with connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO consents(
                    consent_key, granted_at, revoked_at
                ) VALUES ('', '2026-08-07T09:00:00+00:00', NULL)"""
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO consents(
                    consent_key, granted_at, revoked_at
                ) VALUES ('test', '', NULL)"""
            )
