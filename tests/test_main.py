from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.api import images
from app.models import Upload, utcnow
from main import create_app


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.json() == {"status": "ok"}


def test_startup_deletes_expired_empty_sessions(db_engine: Engine) -> None:
    with Session(db_engine) as db:
        expired = Upload(expires_at=utcnow() - timedelta(minutes=1))
        db.add(expired)
        db.commit()
        expired_id = expired.id

    with TestClient(create_app()):
        pass

    with Session(db_engine) as db:
        assert db.get(Upload, expired_id) is None


def test_http_error_returns_detail_and_code(client: TestClient) -> None:
    response = client.get("/images/no-such-id")

    assert response.status_code == 404
    assert response.json() == {"detail": "Image not found", "code": "not_found"}


def test_unknown_path_returns_detail_and_code(client: TestClient) -> None:
    assert client.get("/no-such-path").json() == {"detail": "Not Found", "code": "not_found"}


def test_auth_error_keeps_www_authenticate_header(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/images")

    assert response.json() == {"detail": "Missing or invalid API key", "code": "unauthorized"}
    assert response.headers["WWW-Authenticate"] == "ApiKey"


def test_validation_error_returns_detail_list_and_code(client: TestClient) -> None:
    response = client.get("/images", params={"limit": 101})

    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    assert body["detail"][0]["loc"] == ["query", "limit"]


def test_manual_422_uses_validation_error_code(client: TestClient) -> None:
    response = client.get("/images/some-id/thumbnails/64")

    assert response.json() == {
        "detail": "Thumbnail size must be one of [128, 256, 512]",
        "code": "validation_error",
    }


def test_unhandled_error_returns_500_without_internal_details(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise RuntimeError("C:/secret/path/app.db is broken")

    monkeypatch.setattr(images, "find_image", fail)

    quiet_client = TestClient(client.app, headers=client.headers, raise_server_exceptions=False)

    response = quiet_client.get("/images/some-id")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "code": "internal_server_error"}


def operation_responses(client: TestClient, method: str, path: str) -> dict[str, dict]:
    return client.app.openapi()["paths"][path][method]["responses"]


def test_openapi_lists_every_upload_files_error_status(client: TestClient) -> None:
    responses = operation_responses(client, "post", "/uploads/{upload_id}/files")

    assert {"400", "401", "404", "409", "410", "411", "413"} <= responses.keys()


def test_openapi_lists_401_on_every_protected_operation(client: TestClient) -> None:
    for path, operations in client.app.openapi()["paths"].items():
        if path == "/health":
            continue
        for method, operation in operations.items():
            assert "401" in operation["responses"], f"{method.upper()} {path} does not document 401"


def test_openapi_error_responses_use_error_schema(client: TestClient) -> None:
    error = operation_responses(client, "get", "/images/{image_id}")["404"]
    validation_error = operation_responses(client, "get", "/images")["422"]

    for response in (error, validation_error):
        assert response["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}


def test_openapi_tags_operations_by_router(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]

    assert paths["/uploads"]["post"]["tags"] == ["uploads"]
    assert paths["/images/{image_id}"]["get"]["tags"] == ["images"]
    assert paths["/health"]["get"]["tags"] == ["health"]
