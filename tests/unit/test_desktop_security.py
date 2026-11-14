import hmac
import runpy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from scan64.api import web
from scan64.api.desktop_security import (
    SHELL_ORIGINS,
    SHELL_TOKEN_HEADER,
    ShellTokenMiddleware,
    add_desktop_security,
)

TOKEN = "unit-test-shell-token"
METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def make_app(token: str | None = TOKEN) -> FastAPI:
    application = FastAPI()

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.api_route("/protected", methods=METHODS)
    def protected(request: Request) -> dict[str, str | None]:
        return {"authorization": request.headers.get("authorization")}

    add_desktop_security(application, token)
    return application


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("token", [None, "wrong", "", TOKEN + "suffix"])
def test_every_actual_request_requires_exact_shell_token(method: str, token: str | None) -> None:
    headers = {} if token is None else {SHELL_TOKEN_HEADER: token}
    response = TestClient(make_app()).request(method, "/protected", headers=headers)
    assert response.status_code == 401
    assert TOKEN not in response.text


@pytest.mark.parametrize("method", METHODS)
def test_valid_shell_token_allows_actual_requests(method: str) -> None:
    with TestClient(make_app()) as client:
        response = client.request(method, "/protected", headers={SHELL_TOKEN_HEADER: TOKEN})
    assert response.status_code == 200


def test_shell_token_does_not_replace_or_modify_player_bearer_token() -> None:
    client = TestClient(make_app())
    assert client.get("/protected", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    response = client.get(
        "/protected",
        headers={SHELL_TOKEN_HEADER: TOKEN, "Authorization": "Bearer player-access-token"},
    )
    assert response.json() == {"authorization": "Bearer player-access-token"}
    assert client.get(f"/protected?token={TOKEN}").status_code == 401


def test_duplicate_tokens_are_rejected_even_if_one_or_both_are_correct() -> None:
    client = TestClient(make_app())
    for second in [TOKEN, "wrong"]:
        response = client.get(
            "/protected", headers=[(SHELL_TOKEN_HEADER, TOKEN), (SHELL_TOKEN_HEADER, second)]
        )
        assert response.status_code == 401


def test_header_name_is_case_insensitive() -> None:
    response = TestClient(make_app()).get("/protected", headers={"x-scan64-shell-token": TOKEN})
    assert response.status_code == 200


def test_only_exact_get_health_is_public_and_non_sensitive() -> None:
    client = TestClient(make_app())
    response = client.get("/health")
    assert response.json() == {"status": "ok"}
    assert client.get("/health", headers={SHELL_TOKEN_HEADER: "wrong"}).status_code == 200
    for method in ["HEAD", "POST", "OPTIONS"]:
        assert client.request(method, "/health").status_code == 401
    for path in ["/health/", "/health/missing", "/docs", "/openapi.json", "/missing"]:
        assert client.get(path, follow_redirects=False).status_code == 401


@pytest.mark.parametrize("origin", SHELL_ORIGINS)
def test_shell_preflights_need_no_secret_and_allow_required_headers(origin: str) -> None:
    response = TestClient(make_app()).options(
        "/protected",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                f"{SHELL_TOKEN_HEADER}, Authorization, Content-Type, "
                "Idempotency-Key, client_move_id"
            ),
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "*" not in response.headers["access-control-allow-origin"]
    assert SHELL_TOKEN_HEADER.lower() in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers
    assert "Origin" in response.headers["vary"]


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example",
        "null",
        "http://localhost",
        "http://127.0.0.1",
        "tauri://localhost.attacker.example",
        "http://tauri.localhost:1234",
        "https://tauri.localhost.attacker.example",
    ],
)
def test_other_origins_cannot_preflight(origin: str) -> None:
    response = TestClient(make_app()).options(
        "/protected",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": SHELL_TOKEN_HEADER,
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", SHELL_ORIGINS)
def test_cors_wraps_token_failures_and_successes(origin: str) -> None:
    client = TestClient(make_app())
    for headers, status in [({}, 401), ({SHELL_TOKEN_HEADER: TOKEN}, 200)]:
        response = client.get("/protected", headers={"Origin": origin, **headers})
        assert response.status_code == status
        assert response.headers["access-control-allow-origin"] == origin


def test_disallowed_actual_origin_gets_neither_access_nor_cors_without_token() -> None:
    response = TestClient(make_app()).post(
        "/protected", headers={"Origin": "https://attacker.example"}
    )
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


def test_unapproved_preflight_header_and_method_are_rejected() -> None:
    client = TestClient(make_app())
    for method, header in [("TRACE", SHELL_TOKEN_HEADER), ("POST", "X-Unapproved")]:
        response = client.options(
            "/protected",
            headers={
                "Origin": SHELL_ORIGINS[0],
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": header,
            },
        )
        assert response.status_code == 400


def test_non_preflight_options_is_not_exempt() -> None:
    client = TestClient(make_app())
    for headers in [{}, {"Origin": SHELL_ORIGINS[0]}]:
        assert client.options("/protected", headers=headers).status_code == 401


def test_browser_mode_installs_neither_gate_nor_cors() -> None:
    application = make_app(None)
    assert application.user_middleware == []
    client = TestClient(application)
    assert client.get("/protected").status_code == 200
    response = client.options(
        "/protected",
        headers={"Origin": SHELL_ORIGINS[0], "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in response.headers


def test_empty_token_fails_closed_instead_of_selecting_browser_mode() -> None:
    with pytest.raises(ValueError, match="SCAN64_SHELL_TOKEN"):
        make_app("")
    with pytest.raises(ValueError, match="SCAN64_SHELL_TOKEN"):
        ShellTokenMiddleware(FastAPI(), "")


def test_websocket_handshakes_cannot_bypass_shell_gate() -> None:
    with pytest.raises(WebSocketDisconnect) as error:
        with TestClient(make_app()).websocket_connect("/protected"):
            pytest.fail("Unauthenticated websocket was accepted")
    assert error.value.code == 1008


@pytest.mark.parametrize("token", [None, TOKEN])
def test_real_app_gates_only_desktop_and_never_mounts_its_web_client(
    token: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if token is None:
        monkeypatch.delenv("SCAN64_SHELL_TOKEN", raising=False)
    else:
        monkeypatch.setenv("SCAN64_SHELL_TOKEN", token)
    monkeypatch.delenv("SCAN64_DISTRIBUTION", raising=False)
    mount = Mock()
    monkeypatch.setattr(web, "web_dist_directory", lambda: tmp_path)
    monkeypatch.setattr(web, "mount_web_client", mount)
    namespace = runpy.run_path(str(Path(web.__file__).with_name("app.py")))
    application = namespace["app"]
    client = TestClient(application)
    assert client.get("/health").json() == {"status": "ok"}
    if token is None:
        mount.assert_called_once_with(application, tmp_path)
        assert client.get("/v1/app/info").json() == {"distribution": "source"}
        assert client.get("/missing").status_code == 404
    else:
        mount.assert_not_called()
        assert client.get("/v1/app/info").status_code == 401
        headers = {SHELL_TOKEN_HEADER: token}
        assert client.get("/v1/app/info", headers=headers).json() == {"distribution": "desktop"}
        assert client.get("/", headers=headers).status_code == 404


def test_token_comparison_uses_constant_time_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    compare = Mock(wraps=hmac.compare_digest)
    monkeypatch.setattr(hmac, "compare_digest", compare)
    response = TestClient(make_app()).get("/protected", headers={SHELL_TOKEN_HEADER: "wrong"})
    assert response.status_code == 401
    compare.assert_called_once_with(b"wrong", TOKEN.encode("utf-8"))


@pytest.mark.asyncio
async def test_non_ascii_header_bytes_are_rejected_instead_of_raising() -> None:
    inner = AsyncMock()
    send = AsyncMock()
    middleware = ShellTokenMiddleware(inner, TOKEN)
    await middleware(
        {
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [(b"x-scan64-shell-token", b"\xff")],
        },
        AsyncMock(),
        send,
    )
    inner.assert_not_called()
    assert send.call_args_list[0].args[0]["status"] == 401
