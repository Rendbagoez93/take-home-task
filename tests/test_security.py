import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import Route

from app.config import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_PATHS = {"/health", "/docs", "/docs/oauth2-redirect", "/openapi.json"}


def test_request_without_api_key_returns_401(anonymous_client: TestClient) -> None:
    response = anonymous_client.post("/uploads")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "ApiKey"


def test_request_with_wrong_api_key_returns_401(anonymous_client: TestClient) -> None:
    response = anonymous_client.post("/uploads", headers={"X-API-Key": "wrong-key"})

    assert response.status_code == 401


def test_request_with_valid_api_key_passes(client: TestClient) -> None:
    assert client.post("/uploads").status_code == 201


def test_request_with_any_configured_api_key_passes(
    anonymous_client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "api_keys", ["first-key", "second-key"])

    response = anonymous_client.post("/uploads", headers={"X-API-Key": "second-key"})

    assert response.status_code == 201


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json"])
def test_public_path_without_api_key_returns_200(anonymous_client: TestClient, path: str) -> None:
    assert anonymous_client.get(path).status_code == 200


def all_endpoints(app: FastAPI) -> set[tuple[str, str]]:
    # app.routes holds included routers as nested entries (FastAPI 0.162), so their
    # routes are read from the OpenAPI schema, which lists every operation in them.
    endpoints = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    for route in app.routes:
        if isinstance(route, Route):
            endpoints |= {(method, route.path) for method in route.methods or ()}
    return endpoints


def test_every_non_public_route_requires_api_key(anonymous_client: TestClient) -> None:
    protected = {
        (method, path) for method, path in all_endpoints(anonymous_client.app) if path not in PUBLIC_PATHS
    }

    for method, path in protected:
        response = anonymous_client.request(method, re.sub(r"\{[^}]+\}", "some-id", path))
        assert response.status_code == 401, f"{method} {path} is not protected"
    assert ("POST", "/uploads/{upload_id}/files") in protected


def test_app_refuses_to_start_without_api_keys(tmp_path: Path) -> None:
    env = {**os.environ, "API_KEYS": "", "PYTHONPATH": str(PROJECT_ROOT)}

    # cwd=tmp_path so that no .env file can supply a key.
    result = subprocess.run(
        [sys.executable, "-c", "import main"], cwd=tmp_path, env=env, capture_output=True, text=True
    )

    assert result.returncode != 0
    assert "api_keys" in result.stderr
