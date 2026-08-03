from fastapi.testclient import TestClient


def test_health_and_empty_register(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok"}
    assert "No applications yet" in client.get("/").text


def test_create_view_update_and_stage(client: TestClient) -> None:
    response = client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "is_fully_remote": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    detail = client.get(location)
    assert "Engineer at Acme" in detail.text
    response = client.post(
        location,
        data={
            "role": "Principal Engineer",
            "company": "Acme",
            "notes": "Updated",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    stage = client.post(
        f"{location}/stages",
        data={
            "stage": "Interview",
            "effective_from": "2099-01-01T09:00:00",
            "stage_description": "Screen",
        },
        headers={"HX-Request": "true"},
    )
    assert stage.status_code == 200
    assert "Interview" in stage.text


def test_invalid_create_and_absent_delete_route(client: TestClient) -> None:
    response = client.post("/applications", data={"company": "Acme"})
    assert response.status_code == 422
    assert "Role is required" in response.text
    assert client.delete("/applications/1").status_code == 405


def test_htmx_create_and_update_redirect_to_application_detail(
    client: TestClient,
) -> None:
    created = client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        headers={"HX-Request": "true"},
    )
    assert created.status_code == 200
    assert created.headers["HX-Redirect"] == "/applications/1"
    assert created.text == ""

    updated = client.post(
        "/applications/1",
        data={"role": "Principal Engineer", "company": "Acme"},
        headers={"HX-Request": "true"},
    )
    assert updated.status_code == 200
    assert updated.headers["HX-Redirect"] == "/applications/1"
    assert updated.text == ""


def test_htmx_validation_returns_the_targeted_form_fragment(
    client: TestClient,
) -> None:
    invalid_create = client.post(
        "/applications",
        data={"company": "Acme"},
        headers={"HX-Request": "true"},
    )
    assert invalid_create.status_code == 200
    assert 'id="application-form"' in invalid_create.text
    assert "Role is required" in invalid_create.text
    assert "<!doctype html>" not in invalid_create.text

    created = client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme"},
        follow_redirects=False,
    )
    invalid_update = client.post(
        created.headers["location"],
        data={
            "role": "Engineer",
            "company": "Acme",
            "job_url": "javascript:alert(1)",
        },
        headers={"HX-Request": "true"},
    )
    assert invalid_update.status_code == 200
    assert 'id="application-edit-form"' in invalid_update.text
    assert (
        "Job URL must be an absolute HTTP or HTTPS URL"
        in invalid_update.text
    )
    assert "<!doctype html>" not in invalid_update.text


def test_link_validation_and_optional_job_url(client: TestClient) -> None:
    invalid = client.post(
        "/applications",
        data={
            "role": "Engineer",
            "company": "Acme",
            "job_url": "javascript:alert(1)",
        },
    )
    assert invalid.status_code == 422
    assert "Job URL must be an absolute HTTP or HTTPS URL" in invalid.text

    created = client.post(
        "/applications",
        data={"role": "Engineer", "company": "Acme", "job_url": ""},
        follow_redirects=False,
    )
    assert created.status_code == 303
