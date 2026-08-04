from datetime import date
from pathlib import Path

import pytest

from app.cv_uploads import upload_directory


def test_upload_directory_uses_private_safe_company_and_role_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(tmp_path / "artefacts"))

    directory = upload_directory(
        "Acme / Europe", "Senior Engineer: Platform", date(2026, 8, 4)
    )

    assert directory == (
        tmp_path
        / "artefacts"
        / "Acme-Europe"
        / "2026-08-04 Senior-Engineer-Platform"
    )
