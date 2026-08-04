from pathlib import Path

import httpx2
import pytest

pytestmark = pytest.mark.anyio


async def test_dashboard_uses_preview_and_download_cv_actions(
    client: httpx2.AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cv_root = tmp_path / "artefacts"
    cv_root.mkdir()
    monkeypatch.setenv("JOBHUNTER_CV_ROOT", str(cv_root))
    pdf = cv_root / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    docx = cv_root / "resume.docx"
    docx.write_bytes(b"word content")

    first = await client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "cv_path": str(pdf)},
        follow_redirects=False,
    )
    second = await client.post(
        "/applications",
        data={"role": "Writer", "company": "Beta", "cv_path": str(docx)},
        follow_redirects=False,
    )

    dashboard = await client.get("/")

    assert (
        f'data-preview-url="{first.headers["location"]}/cv/preview"'
        in dashboard.text
    )
    assert f'href="{second.headers["location"]}/cv/download"' in dashboard.text
