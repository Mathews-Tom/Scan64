import pytest
from fastapi.testclient import TestClient

from scan64.api.app import app


@pytest.mark.parametrize("distribution", [None, "source", "docker"])
def test_updates_are_desktop_only(
    monkeypatch: pytest.MonkeyPatch, distribution: str | None
) -> None:
    if distribution is None:
        monkeypatch.delenv("SCAN64_DISTRIBUTION", raising=False)
    else:
        monkeypatch.setenv("SCAN64_DISTRIBUTION", distribution)
    assert TestClient(app).get("/v1/app/update").status_code == 404


def test_desktop_update_returns_read_only_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    import scan64.desktop_update as updates

    monkeypatch.setenv("SCAN64_DISTRIBUTION", "desktop")
    state = {
        "status": "ready",
        "message": "Restart to install",
        "version": "0.5.1",
        "automatic_updates": True,
        "available": True,
        "release_url": "https://github.com/Mathews-Tom/Scan64/releases/tag/v0.5.1",
    }
    calls = []

    def snapshot() -> dict[str, object]:
        calls.append(True)
        return state

    monkeypatch.setattr(updates, "get_update_state", snapshot)
    client = TestClient(app)
    assert client.get("/v1/app/update").json() == state
    assert calls == [True]
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/v1/app/update").status_code == 405
    assert calls == [True]
