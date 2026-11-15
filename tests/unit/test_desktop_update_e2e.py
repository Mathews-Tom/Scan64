"""The update receipt must prove real installation, not just a successful check."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/desktop-update-e2e.py"
spec = importlib.util.spec_from_file_location("desktop_update_e2e", SCRIPT)
assert spec is not None and spec.loader is not None
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


def trace() -> list[dict[str, object]]:
    return [
        {"event": "startup", "version": updater.FROM_VERSION},
        {"state": "checking"},
        {"state": "downloading", "percent": 0},
        {"state": "downloading", "percent": 42},
        {"state": "ready"},
        {"event": "restart-click"},
        {"state": "installing"},
        {"event": "startup", "version": updater.TO_VERSION},
        {"event": "complete"},
    ]


def test_install_acceptance_requires_observed_progress_click_and_new_version() -> None:
    updater.assert_trace(trace(), "install")


@pytest.mark.parametrize("missing", ["checking", "downloading", "ready", "installing"])
def test_incomplete_state_trace_cannot_pass(missing: str) -> None:
    rows = [row for row in trace() if row.get("state") != missing]
    with pytest.raises((AssertionError, ValueError)):
        updater.assert_trace(rows, "install")


def test_full_download_only_does_not_count_as_progress() -> None:
    rows = trace()
    for row in rows:
        if row.get("state") == "downloading":
            row["percent"] = 100
    with pytest.raises(AssertionError):
        updater.assert_trace(rows, "install")


def test_install_requires_relaunch_not_just_version_in_available_state() -> None:
    rows = [row for row in trace() if row.get("version") != updater.TO_VERSION]
    rows[1]["availableVersion"] = updater.TO_VERSION
    with pytest.raises(AssertionError):
        updater.assert_trace(rows, "install")


def test_install_requires_the_explicit_restart_action() -> None:
    rows = [row for row in trace() if row.get("event") != "restart-click"]
    with pytest.raises(AssertionError):
        updater.assert_trace(rows, "install")


def test_rejection_cannot_pass_after_install_or_relaunch() -> None:
    rows = trace()
    rows.append({"state": "failed", "reason": "signature mismatch"})
    with pytest.raises(AssertionError):
        updater.assert_trace(rows, "reject")
    updater.assert_trace(
        [
            {"event": "startup", "version": updater.FROM_VERSION},
            {"state": "checking"},
            {"state": "downloading", "percent": 10},
            {"state": "failed", "reason": "signature mismatch"},
        ],
        "reject",
    )


def test_network_failure_does_not_count_as_signature_rejection() -> None:
    rows = [
        {"event": "startup", "version": updater.FROM_VERSION},
        {"state": "checking"},
        {"state": "downloading", "percent": 10},
        {"state": "failed", "reason": "Connection reset"},
    ]
    with pytest.raises(AssertionError):
        updater.assert_trace(rows, "reject")


def test_deb_unavailable_requires_explanation_and_real_download_link() -> None:
    rows = [
        {"event": "startup", "version": updater.FROM_VERSION},
        {
            "state": "unavailable",
            "reason": "Linux .deb install",
            "downloadUrl": updater.DOWNLOAD_URL,
        },
    ]
    updater.assert_trace(rows, "unavailable")
    rows[1]["downloadUrl"] = "http://example.invalid/"
    with pytest.raises(AssertionError):
        updater.assert_trace(rows, "unavailable")


def test_signature_tampering_changes_bytes_without_breaking_framing() -> None:
    signature_bytes = bytes(range(74))
    framed = "untrusted comment: fixture\n" + base64.b64encode(signature_bytes).decode() + "\n"
    original = base64.b64encode(framed.encode()).decode()
    changed = updater.signature_tamper(original)
    assert changed != original
    decoded = base64.b64decode(changed).decode().splitlines()
    assert decoded[0] == "untrusted comment: fixture"
    actual = base64.b64decode(decoded[1])
    assert actual[:-1] == signature_bytes[:-1]
    assert actual[-1] == signature_bytes[-1] ^ 1


def test_manifest_uses_signature_content_and_local_host_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updater, "platform_key", lambda: "darwin-aarch64")
    manifest = updater.manifest(tmp_path / "Scan64 update.tar.gz", "signed-content\n", 1234)
    assert manifest["version"] == updater.TO_VERSION
    entry = manifest["platforms"]["darwin-aarch64"]
    assert entry == {
        "signature": "signed-content",
        "url": "http://127.0.0.1:1234/Scan64%20update.tar.gz",
    }


def test_events_tolerate_only_the_current_partial_append(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"state": "checking"}) + '\n{"state":')
    assert updater.events(path) == [{"state": "checking"}]
    path.write_text('{"state":\n{"state":"ready"}\n')
    with pytest.raises(json.JSONDecodeError):
        updater.events(path)


def test_player_survival_checks_the_real_player_and_profile_schema(tmp_path: Path) -> None:
    from sqlmodel import Session, create_engine

    from scan64.api.models import Player, PlayerProfile

    database = tmp_path / "scan64.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    Player.__table__.create(engine)
    PlayerProfile.__table__.create(engine)
    with Session(engine) as session:
        session.add(Player(id=updater.PLAYER_ID))
        session.add(PlayerProfile(player_id=updater.PLAYER_ID, display_name="Update smoke"))
        session.commit()
    assert updater.player_present(database)
    with Session(engine) as session:
        profile = session.get(PlayerProfile, updater.PLAYER_ID)
        assert profile is not None
        profile.display_name = "Changed unexpectedly"
        session.add(profile)
        session.commit()
    assert not updater.player_present(database)
    engine.dispose()
