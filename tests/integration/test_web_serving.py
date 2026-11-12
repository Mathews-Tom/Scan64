from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Mount

from scan64.api.app import app
from scan64.api.web import mount_web_client


@pytest.fixture
def web_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    (tmp_path / "index.html").write_text("<!doctype html><title>Scan64 test client</title>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('Scan64')")
    # Even a built file with a reserved name may not take over the API.
    (tmp_path / "v1").mkdir()
    (tmp_path / "v1" / "missing").write_text("shadowed")
    monkeypatch.setattr(
        app.router,
        "routes",
        [route for route in app.routes if not (isinstance(route, Mount) and route.name == "web")],
    )
    mount_web_client(app, tmp_path)
    yield TestClient(app)


@pytest.mark.parametrize("path", ["/", "/games/123/analysis", "/library/import"])
def test_client_navigation_returns_index(web_client: TestClient, path: str) -> None:
    response = web_client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Scan64 test client" in response.text
    assert web_client.head(path).status_code == 200


def test_real_assets_and_missing_assets(web_client: TestClient) -> None:
    assert web_client.get("/assets/app.js").text == "console.log('Scan64')"
    for path in ["/assets/missing.js", "/assets/missing", "/missing.css"]:
        response = web_client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    "path",
    ["/v1", "/v1/missing", "/health/missing", "/docs/missing", "/openapi.json/missing"],
)
def test_reserved_namespaces_never_fall_back(web_client: TestClient, path: str) -> None:
    response = web_client.get(path)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_api_documentation_and_health_are_preserved(web_client: TestClient) -> None:
    assert web_client.get("/health").json() == {"status": "ok"}
    assert "swagger-ui" in web_client.get("/docs").text
    assert web_client.get("/openapi.json").json()["info"]["title"] == "Scan64 API"
    assert web_client.post("/v1/missing", json={}).status_code == 404
    assert web_client.post("/health", json={}).status_code == 405


def test_static_paths_cannot_escape_the_build(web_client: TestClient, tmp_path: Path) -> None:
    (tmp_path.parent / "secret.txt").write_text("not public")
    response = web_client.get("/%2e%2e/secret.txt")
    assert response.status_code == 404
    assert "not public" not in response.text
