"""Persistence and enforcement for remote profile-sharing consent."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.database import connect

OPENAI_PROFILE_SHARING_CONSENT = "openai_profile_sharing_v1"


class ConsentRequiredError(RuntimeError):
    """Raised before private profile content is sent without consent."""

    def __init__(self) -> None:
        super().__init__("OpenAI profile-sharing acknowledgement is required.")


@dataclass(frozen=True)
class ConsentState:
    """Current state of one versioned acknowledgement."""

    key: str
    granted_at: str | None
    revoked_at: str | None

    @property
    def is_granted(self) -> bool:
        """Return whether the acknowledgement is currently active."""
        return self.granted_at is not None and self.revoked_at is None


class ConsentRepository:
    """Store one current consent state in the private SQLite database."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = database_path

    def get_openai_profile_sharing(self) -> ConsentState:
        """Return the current OpenAI profile-sharing acknowledgement."""
        with connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT consent_key, granted_at, revoked_at
                FROM consents WHERE consent_key = ?""",
                (OPENAI_PROFILE_SHARING_CONSENT,),
            ).fetchone()
        if row is None:
            return ConsentState(
                key=OPENAI_PROFILE_SHARING_CONSENT,
                granted_at=None,
                revoked_at=None,
            )
        return ConsentState(
            key=str(row["consent_key"]),
            granted_at=str(row["granted_at"]),
            revoked_at=(
                str(row["revoked_at"])
                if row["revoked_at"] is not None
                else None
            ),
        )

    def acknowledge_openai_profile_sharing(
        self, acknowledged_at: str
    ) -> ConsentState:
        """Grant consent, preserving the timestamp of a repeated grant."""
        _require_timestamp(acknowledged_at)
        with connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO consents(
                    consent_key, granted_at, revoked_at
                ) VALUES (?, ?, NULL)
                ON CONFLICT(consent_key) DO UPDATE SET
                    granted_at = excluded.granted_at,
                    revoked_at = NULL
                WHERE consents.revoked_at IS NOT NULL""",
                (OPENAI_PROFILE_SHARING_CONSENT, acknowledged_at),
            )
        return self.get_openai_profile_sharing()

    def revoke_openai_profile_sharing(self, revoked_at: str) -> ConsentState:
        """Revoke an active acknowledgement; repeated revocation is a no-op."""
        _require_timestamp(revoked_at)
        try:
            with connect(self.database_path) as connection:
                connection.execute(
                    """UPDATE consents SET revoked_at = ?
                    WHERE consent_key = ? AND revoked_at IS NULL""",
                    (revoked_at, OPENAI_PROFILE_SHARING_CONSENT),
                )
        except sqlite3.IntegrityError as error:
            raise ValueError(
                "revocation timestamp cannot precede acknowledgement"
            ) from error
        return self.get_openai_profile_sharing()

    def require_openai_profile_sharing(self) -> None:
        """Stop profile-bearing provider work unless consent is active."""
        if not self.get_openai_profile_sharing().is_granted:
            raise ConsentRequiredError


def _require_timestamp(value: str) -> None:
    if not value or value != value.strip():
        raise ValueError("consent timestamp must be non-empty trimmed text")
