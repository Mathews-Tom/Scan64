from __future__ import annotations

import asyncio
import json
from pathlib import Path
from secrets import token_hex

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from scan64.api import explanation_settings
from scan64.api.desktop_security import SHELL_TOKEN_HEADER, add_desktop_security
from scan64.api.middleware import IdempotencyRecord
from scan64.explanations.settings import (
    OPERATOR_LLM_CONFIG,
    OwnerExplanationSettings,
    read_local_explanation_provider,
)
from scan64.persistence.database import get_session
from scan64.providers.llm import ExplanationRequest, LLMMessage, LLMProviderError
from scan64.providers.llm import local_ollama as local

OWNER = "wording-owner"
OTHER = "wording-other"
LOCAL_MODEL = "test-local-model:latest"
OWNER_PATH = f"/v1/players/{OWNER}/explanation-settings"
OTHER_PATH = f"/v1/players/{OTHER}/explanation-settings"
DISABLED = {"mode": "disabled", "model": None}
ENABLED = {"mode": "local", "model": LOCAL_MODEL}


def _register(client: TestClient, player: str) -> dict[str, str]:
    response = client.post(
        "/v1/players",
        json={"id": player, "preferences": {"local_explanation_model": "untrusted preference"}},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_local_wording_requires_owner_consent_and_reads_current_committed_setting(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)
    calls = 0

    async def models() -> list[str]:
        nonlocal calls
        calls += 1
        return [LOCAL_MODEL]

    monkeypatch.setattr(explanation_settings, "detect_local_ollama", models)
    owner = _register(client, OWNER)
    other = _register(client, OTHER)
    assert client.get(OWNER_PATH, headers=owner).json() == DISABLED
    assert await read_local_explanation_provider(db_session, OWNER) is None
    assert client.get(OWNER_PATH, headers=other).status_code == 403
    assert client.post(f"{OWNER_PATH}/probe", headers=other).status_code == 403
    change = {"model": LOCAL_MODEL, "consent": True}
    assert client.put(OWNER_PATH, json=change, headers=other).status_code == 403
    refused = client.put(OWNER_PATH, json={**change, "consent": False}, headers=owner)
    assert refused.status_code == 422
    assert calls == 0

    oversized = client.put(OWNER_PATH, json={**change, "model": "x" * 2048}, headers=owner)
    assert oversized.status_code == 413
    assert client.post(f"{OWNER_PATH}/probe", headers=owner).json() == {
        "available": True,
        "models": [LOCAL_MODEL],
    }
    assert client.get(OWNER_PATH, headers=owner).json() == DISABLED
    assert client.put(OWNER_PATH, json=change, headers=owner).json() == ENABLED
    assert await read_local_explanation_provider(db_session, OWNER) is not None
    assert await read_local_explanation_provider(db_session, OTHER) is None
    assert client.get(OTHER_PATH, headers=other).json() == DISABLED

    owner_with_key = {**owner, "Idempotency-Key": "local-wording-choice"}
    assert client.put(OWNER_PATH, json=change, headers=owner_with_key).json() == ENABLED
    count = calls
    disable = {"model": None, "consent": False}
    with Session(db_session.get_bind()) as running_job:
        # Hold the old WAL snapshot across disable, as a long analysis can.
        running_job.connection().exec_driver_sql("BEGIN")
        assert running_job.get(OwnerExplanationSettings, OWNER) is not None
        assert client.put(OWNER_PATH, json=disable, headers=owner_with_key).json() == DISABLED
        assert await read_local_explanation_provider(running_job, OWNER) is None
    assert await read_local_explanation_provider(db_session, OWNER) is None
    assert calls == count


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["none", "same-model", "same-owner-id"])
async def test_consent_replaced_during_catalog_is_not_reused_for_chat(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)
    catalog_started = asyncio.Event()
    finish_catalog = asyncio.Event()
    paths: list[str] = []

    async def installed() -> list[str]:
        return [LOCAL_MODEL]

    async def daemon(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.path == "/api/tags" and not request.content
        catalog_started.set()
        await finish_catalog.wait()
        return httpx.Response(200, json={"models": [{"name": LOCAL_MODEL}]})

    monkeypatch.setattr(explanation_settings, "detect_local_ollama", installed)
    monkeypatch.setattr(local.httpx, "AsyncHTTPTransport", lambda **_: httpx.MockTransport(daemon))
    owner = _register(client, OWNER)
    change = {"model": LOCAL_MODEL, "consent": True}
    assert client.put(OWNER_PATH, json=change, headers=owner).json() == ENABLED
    provider = await read_local_explanation_provider(db_session, OWNER)
    assert provider is not None
    request = ExplanationRequest(messages=(LLMMessage(role="user", content="test evidence"),))
    async with asyncio.TaskGroup() as tasks:

        async def attempt_generation() -> None:
            with pytest.raises(LLMProviderError, match="consent changed"):
                await provider.generate(request)

        tasks.create_task(attempt_generation())
        try:
            await asyncio.wait_for(catalog_started.wait(), timeout=2)
            if replacement == "same-owner-id":
                deleted = client.request(
                    "DELETE",
                    f"/v1/players/{OWNER}/data",
                    json={"dry_run": False, "confirmation": f"delete-{OWNER}"},
                    headers=owner,
                )
                assert deleted.status_code == 200, deleted.text
                owner = _register(client, OWNER)
            else:
                disable = {"model": None, "consent": False}
                assert client.put(OWNER_PATH, json=disable, headers=owner).json() == DISABLED
            if replacement != "none":
                assert client.put(OWNER_PATH, json=change, headers=owner).json() == ENABLED
        finally:
            finish_catalog.set()
    assert paths == ["/api/tags"]


def test_missing_or_operator_managed_peer_never_enables_local_wording(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)
    calls = 0

    async def unavailable() -> None:
        nonlocal calls
        calls += 1
        return None

    monkeypatch.setattr(explanation_settings, "detect_local_ollama", unavailable)
    owner = _register(client, OWNER)
    change = {"model": LOCAL_MODEL, "consent": True}
    assert client.post(f"{OWNER_PATH}/probe", headers=owner).json() == {
        "available": False,
        "models": [],
    }
    assert client.put(OWNER_PATH, json=change, headers=owner).status_code == 503
    assert client.get(OWNER_PATH, headers=owner).json() == DISABLED

    monkeypatch.setenv(OPERATOR_LLM_CONFIG, str(tmp_path / "operator.toml"))
    assert client.get(OWNER_PATH, headers=owner).json() == {"mode": "operator", "model": None}
    assert client.post(f"{OWNER_PATH}/probe", headers=owner).status_code == 409
    assert client.put(OWNER_PATH, json=change, headers=owner).status_code == 409
    assert calls == 2


def test_desktop_probe_requires_both_shell_and_owner_authorization(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)
    calls = 0

    async def models() -> list[str]:
        nonlocal calls
        calls += 1
        return [LOCAL_MODEL]

    monkeypatch.setattr(explanation_settings, "detect_local_ollama", models)
    owner = _register(client, OWNER)
    other = _register(client, OTHER)
    desktop = FastAPI()
    desktop.include_router(explanation_settings.router)
    desktop.dependency_overrides[get_session] = lambda: db_session
    shell = {SHELL_TOKEN_HEADER: token_hex(32)}
    add_desktop_security(desktop, shell[SHELL_TOKEN_HEADER])
    with TestClient(desktop) as native:
        assert native.post(f"{OWNER_PATH}/probe", headers=owner).status_code == 401
        assert native.post(f"{OWNER_PATH}/probe", headers=shell).status_code == 401
        assert native.post(f"{OWNER_PATH}/probe", headers={**shell, **other}).status_code == 403
        assert calls == 0
        allowed = native.post(f"{OWNER_PATH}/probe", headers={**shell, **owner})
        assert allowed.json() == {"available": True, "models": [LOCAL_MODEL]}
        assert calls == 1


def test_consent_survives_learning_reset_but_not_account_archive_restore(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(OPERATOR_LLM_CONFIG, raising=False)

    async def models() -> list[str]:
        return [LOCAL_MODEL]

    monkeypatch.setattr(explanation_settings, "detect_local_ollama", models)
    owner = _register(client, OWNER)
    change = {"model": LOCAL_MODEL, "consent": True}
    assert client.put(OWNER_PATH, json=change, headers=owner).json() == ENABLED
    reset_path = f"/v1/players/{OWNER}/progress/reset"
    reset = client.post(reset_path, json={"confirmation": "RESET PROGRESS"}, headers=owner)
    assert reset.status_code == 200, reset.text
    assert client.get(OWNER_PATH, headers=owner).json() == ENABLED

    export_key = "private-archive"
    export_headers = {**owner, "Idempotency-Key": export_key}
    export = client.post("/v1/exports", json={"player_id": OWNER}, headers=export_headers)
    assert export.status_code == 200, export.text
    archive = export.json()
    assert LOCAL_MODEL not in json.dumps(archive)
    assert (
        db_session.exec(
            select(IdempotencyRecord).where(
                col(IdempotencyRecord.idempotency_key).endswith(export_key)
            )
        ).first()
        is None
    )
    delete_path = f"/v1/players/{OWNER}/data"
    deletion_headers = {**owner, "Idempotency-Key": "local-wording-deletion"}
    dry_run = client.request(
        "DELETE", delete_path, json={"dry_run": True}, headers=deletion_headers
    )
    assert dry_run.json()["affected_rows"]["owner_explanation_settings"] == 1
    assert client.get(OWNER_PATH, headers=owner).json() == ENABLED
    deleted = client.request(
        "DELETE",
        delete_path,
        json={"dry_run": False, "confirmation": f"delete-{OWNER}"},
        headers=deletion_headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["affected_rows"]["owner_explanation_settings"] == 1
    assert client.get(OWNER_PATH, headers=owner).status_code == 403
    assert (
        client.post("/v1/exports", json={"player_id": OWNER}, headers=export_headers).status_code
        == 403
    )
    restored = client.post("/v1/imports", json=archive, headers=owner)
    assert restored.status_code == 200, restored.text
    assert client.get(OWNER_PATH, headers=owner).json() == DISABLED
