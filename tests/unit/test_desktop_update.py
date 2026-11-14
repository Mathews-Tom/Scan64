"""Updater tests use throwaway Ed25519 keys and disposable bundles only."""

from __future__ import annotations

import base64
import hashlib
import json
import plistlib
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scan64 import desktop_update as update


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def manifest_values(**changes: Any) -> dict[str, Any]:
    return {
        "asset": "Scan64-0.5.0-macos-arm64.zip",
        "format": 1,
        "platform": "macos-arm64",
        "sha256": "a" * 64,
        "size": 100,
        "version": "0.5.0",
        **changes,
    }


def verify(
    key: Ed25519PrivateKey, values: Any, tag: str = "v0.5.0", current: str = "0.4.0"
) -> update.Manifest | None:
    content = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    return update.verify_manifest(
        content,
        base64.b64encode(key.sign(content)),
        base64.b64encode(key.public_key().public_bytes_raw()).decode(),
        tag,
        current,
    )


def test_exact_release_signer_bytes(signing_key: Ed25519PrivateKey) -> None:
    manifest = verify(signing_key, manifest_values())
    assert manifest == update.Manifest("0.5.0", "Scan64-0.5.0-macos-arm64.zip", "a" * 64, 100)


@pytest.mark.parametrize(
    "changes",
    [
        {"format": 2},
        {"format": True},
        {"platform": "linux"},
        {"version": "v0.5.0"},
        {"version": "../0.5.0"},
        {"version": "0.5.1"},
        {"version": 5},
        {"asset": "../Scan64.zip"},
        {"sha256": "A" * 64},
        {"sha256": 42},
        {"size": 0},
        {"size": -1},
        {"size": True},
        {"size": "100"},
        {"extra": 1},
    ],
)
def test_rejects_manifest_fields(signing_key: Ed25519PrivateKey, changes: dict[str, Any]) -> None:
    with pytest.raises(update.UpdateError):
        verify(signing_key, manifest_values(**changes))


@pytest.mark.parametrize("value", [[], None, {"format": 1}])
def test_rejects_manifest_shape(signing_key: Ed25519PrivateKey, value: Any) -> None:
    with pytest.raises(update.UpdateError, match="fields"):
        verify(signing_key, value)


def test_verifies_before_parsing(signing_key: Ed25519PrivateKey) -> None:
    public = base64.b64encode(signing_key.public_key().public_bytes_raw()).decode()
    content = b"not json"
    with pytest.raises(update.UpdateError, match="signature"):
        update.verify_manifest(content, base64.b64encode(bytes(64)), public, "v0.5.0", "0.4.0")
    with pytest.raises(update.UpdateError, match="JSON"):
        update.verify_manifest(
            content, base64.b64encode(signing_key.sign(content)), public, "v0.5.0", "0.4.0"
        )


@pytest.mark.parametrize("signature", [b"bad", b"%%%%", b"\xff", base64.b64encode(bytes(63))])
def test_malformed_signature(signing_key: Ed25519PrivateKey, signature: bytes) -> None:
    public = base64.b64encode(signing_key.public_key().public_bytes_raw()).decode()
    with pytest.raises(update.UpdateError):
        update.verify_manifest(b"{}", signature, public, "v0.5.0", "0.4.0")


def test_wrong_key_and_mutated_bytes(signing_key: Ed25519PrivateKey) -> None:
    content = json.dumps(manifest_values()).encode()
    signature = base64.b64encode(signing_key.sign(content))
    other = Ed25519PrivateKey.generate()
    with pytest.raises(update.UpdateError, match="signature"):
        update.verify_manifest(
            content,
            signature,
            base64.b64encode(other.public_key().public_bytes_raw()).decode(),
            "v0.5.0",
            "0.4.0",
        )
    with pytest.raises(update.UpdateError, match="signature"):
        update.verify_manifest(
            content + b" ",
            signature,
            base64.b64encode(signing_key.public_key().public_bytes_raw()).decode(),
            "v0.5.0",
            "0.4.0",
        )


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("0.5.0", "0.5.0", False),
        ("0.5.0", "0.6.0", False),
        ("0.10.0", "0.9.0", True),
        ("0.5.0rc1", "0.5.0", False),
        ("0.5.0", "0.5.0rc1", True),
    ],
)
def test_package_version_order(
    signing_key: Ed25519PrivateKey, candidate: str, current: str, newer: bool
) -> None:
    result = verify(
        signing_key,
        manifest_values(version=candidate, asset=f"Scan64-{candidate}-macos-arm64.zip"),
        f"v{candidate}",
        current,
    )
    assert (result is not None) is newer


def test_invalid_package_version(signing_key: Ed25519PrivateKey) -> None:
    with pytest.raises(update.UpdateError, match="package version"):
        verify(signing_key, manifest_values(), current="not-a-version")


def test_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle = tmp_path / "Scan64.app"
    assert update.unavailable_reason(bundle, frozen=False)
    assert update.unavailable_reason(bundle, frozen=True, system="Linux", machine="arm64")
    assert update.unavailable_reason(bundle, frozen=True, system="Darwin", machine="x86_64")
    assert update.unavailable_reason(None, frozen=True, system="Darwin", machine="arm64")
    assert "Move" in str(
        update.unavailable_reason(
            Path("/private/var/AppTranslocation/id/Scan64.app"),
            frozen=True,
            system="Darwin",
            machine="arm64",
        )
    )
    monkeypatch.setattr(update.os, "access", lambda *_: False)
    assert "writable" in str(
        update.unavailable_reason(bundle, frozen=True, system="Darwin", machine="arm64")
    )
    monkeypatch.setattr(update.os, "access", lambda *_: True)
    assert update.unavailable_reason(bundle, frozen=True, system="Darwin", machine="arm64") is None


def test_settings(tmp_path: Path) -> None:
    assert update.load_settings(tmp_path)
    for text in ("bad json", "[]", '{"automatic_updates": "false"}'):
        (tmp_path / "settings.json").write_text(text)
        assert update.load_settings(tmp_path)
    update.save_settings(tmp_path, False)
    assert not update.load_settings(tmp_path)
    assert not (tmp_path / "settings.json.tmp").exists()
    update.save_settings(tmp_path, True)
    assert update.load_settings(tmp_path)


class BytesClient(update.ReleaseClient):
    def __init__(self, value: bytes) -> None:
        super().__init__()
        self.value = value

    def chunks(self, url: str):  # type: ignore[no-untyped-def]
        yield self.value[:2]
        yield self.value[2:]


@pytest.mark.parametrize(
    "value,size,digest,error",
    [
        (b"abcd", 3, "a" * 64, "exceeds"),
        (b"abcd", 5, "a" * 64, "size does not"),
        (b"abcd", 4, "a" * 64, "digest"),
    ],
)
def test_download_rejection(
    tmp_path: Path, value: bytes, size: int, digest: str, error: str
) -> None:
    with pytest.raises(update.UpdateError, match=error):
        update.download_archive(
            BytesClient(value),
            "https://example.test",
            update.Manifest("0.5.0", "archive.zip", digest, size),
            tmp_path,
            lambda _: None,
            threading.Event(),
        )
    assert not list(tmp_path.iterdir())


def test_atomic_download(tmp_path: Path) -> None:
    progress: list[int] = []
    value = b"abcd"
    manifest = update.Manifest("0.5.0", "archive.zip", hashlib.sha256(value).hexdigest(), 4)
    path = update.download_archive(
        BytesClient(value),
        "https://example.test",
        manifest,
        tmp_path,
        progress.append,
        threading.Event(),
    )
    assert path.read_bytes() == value
    assert progress == [50, 100]
    stop = threading.Event()
    stop.set()
    with pytest.raises(update.UpdateError, match="cancelled"):
        update.download_archive(
            BytesClient(value), "https://example.test", manifest, tmp_path, progress.append, stop
        )
    assert path.read_bytes() == value


def test_metadata_cap_and_transport() -> None:
    with pytest.raises(update.UpdateError, match="size limit"):
        BytesClient(b"1234").fetch("unused", 3)
    for url in ("http://github.com/a", "file:///tmp/a"):
        with pytest.raises(update.UpdateError, match="HTTPS"):
            update.ReleaseClient().validate_request(httpx.Request("GET", url))
    update.ReleaseClient().validate_request(httpx.Request("GET", "https://github.com/a"))
    update.ReleaseClient(test_loopback=True).validate_request(
        httpx.Request("GET", "http://127.0.0.1/a")
    )
    with pytest.raises(update.UpdateError):
        update.ReleaseClient(test_loopback=True).validate_request(
            httpx.Request("GET", "http://example.test/a")
        )


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"tag_name": "v1"},
        {"tag_name": "v1", "assets": [None]},
        {"tag_name": "v1", "assets": [{"name": "a", "browser_download_url": "b"}] * 2},
    ],
)
def test_invalid_release_assets(value: Any) -> None:
    with pytest.raises(update.UpdateError):
        update.release_assets(value)


def fake_bundle(path: Path, version: str) -> None:
    (path / "Contents").mkdir(parents=True)
    (path / "Contents/Info.plist").write_bytes(
        plistlib.dumps({"CFBundleShortVersionString": version})
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="Uses macOS ditto and PlistBuddy")
def test_stage_version_and_path_checks(tmp_path: Path) -> None:
    bundle = tmp_path / "source/Scan64.app"
    fake_bundle(bundle, "0.5.0")
    archive = tmp_path / "app.zip"
    subprocess.run(
        ["/usr/bin/ditto", "-c", "-k", "--keepParent", str(bundle), str(archive)], check=True
    )
    manifest = update.Manifest("0.5.0", archive.name, "a" * 64, 1)
    staged = update.stage_archive(archive, manifest, tmp_path / "update")
    assert update.bundle_version(staged) == "0.5.0"
    with pytest.raises(update.UpdateError, match="version"):
        update.stage_archive(
            archive, update.Manifest("0.6.0", archive.name, "a" * 64, 1), tmp_path / "update"
        )
    for entry in ("../escape", "/absolute", "other.app/file"):
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(entry, "unsafe")
        with pytest.raises(update.UpdateError, match="path"):
            update.validate_archive(archive)


@pytest.mark.skipif(sys.platform != "darwin", reason="Uses macOS ditto and PlistBuddy")
@pytest.mark.parametrize("failure", [False, True])
def test_helper_backup_install_and_rollback(tmp_path: Path, failure: bool) -> None:
    # Shell metacharacters/spaces in paths must remain literal arguments.
    data = tmp_path / "data ' $ harmless"
    data.mkdir()
    bundle = tmp_path / "Applications/Scan64.app"
    staged = tmp_path / "staging/Scan64.app"
    fake_bundle(bundle, "0.4.0")
    fake_bundle(staged, "0.5.0")
    (bundle / "old-content").write_text("old")
    (staged / "new-content").write_text("new")
    (data / "scan64.db").write_bytes(b"database")
    (data / "scan64.db-wal").write_bytes(b"wal")
    (data / "scan64.db-shm").write_bytes(b"shm")
    marker = tmp_path / "relaunched"
    relaunch = ["/usr/bin/false"] if failure else ["/usr/bin/touch", str(marker)]
    plan = update.InstallPlan(bundle, staged, data, "0.4.0", "0.5.0", 2147483647, relaunch)
    result = subprocess.run(plan.command(), check=False)
    assert (result.returncode != 0) is failure
    assert update.bundle_version(bundle) == ("0.4.0" if failure else "0.5.0")
    if failure:
        assert (bundle / "old-content").read_text() == "old"
    else:
        assert update.bundle_version(Path(f"{bundle}.previous")) == "0.4.0"
        assert marker.exists()
    backups = list((data / "backups").glob("*.db"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b"database"
    assert Path(f"{backups[0]}-wal").read_bytes() == b"wal"
    assert Path(f"{backups[0]}-shm").read_bytes() == b"shm"
    assert (data / "scan64.db").read_bytes() == b"database"


def test_install_plan_requires_absolute_paths(tmp_path: Path) -> None:
    with pytest.raises(update.UpdateError, match="absolute"):
        update.InstallPlan(Path("relative.app"), tmp_path, tmp_path, "0.4.0", "0.5.0", 1).command()


def test_controller_disabled_and_thread_safe_settings(tmp_path: Path) -> None:
    controller = update.UpdateController(tmp_path, "0.4.0", None, disabled_reason="not packaged")
    controller.start()
    controller.check()
    assert controller.snapshot()["status"] == "unavailable"
    controller.set_automatic(False)
    assert controller.snapshot()["automatic_updates"] is False
    copied = controller.snapshot()
    copied["status"] = "changed"
    assert controller.snapshot()["status"] == "unavailable"
    controller.stop()
    with pytest.raises(update.UpdateError, match="verified"):
        controller.restart()


@pytest.mark.parametrize("target", ["/outside", "../../outside"])
def test_rejects_escaping_symlinks(tmp_path: Path, target: str) -> None:
    archive = tmp_path / "unsafe.zip"
    link = zipfile.ZipInfo("Scan64.app/link")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(link, target)
    with pytest.raises(update.UpdateError, match="symlink"):
        update.validate_archive(archive)


def test_rejects_extraction_through_symlink(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.zip"
    link = zipfile.ZipInfo("Scan64.app/alias")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(link, "Contents")
        output.writestr("Scan64.app/ALIAS/file", "must not traverse even case aliases")
    with pytest.raises(update.UpdateError, match="traverses"):
        update.validate_archive(archive)


@pytest.mark.parametrize("automatic", [True, False])
def test_startup_daily_schedule_respects_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, automatic: bool
) -> None:
    update.save_settings(tmp_path, automatic)
    controller = update.UpdateController(tmp_path, "0.4.0", tmp_path / "Scan64.app")
    delays: list[float] = []
    calls: list[bool] = []

    class ScheduleWake:
        def wait(self, delay: float) -> None:
            delays.append(delay)
            if len(delays) == 2:
                controller._stop.set()

        def clear(self) -> None:
            pass

        def set(self) -> None:
            pass

    monkeypatch.setattr(controller, "_wake", ScheduleWake())
    monkeypatch.setattr(controller, "check_background", lambda: calls.append(True))
    controller.start()
    assert controller._scheduler is not None
    controller._scheduler.join(timeout=2)
    assert not controller._scheduler.is_alive()
    assert delays == [5.0, 86400]
    assert calls == ([True] if automatic else [])
    controller.stop()


def test_controller_pipeline_and_failure_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signing_key: Ed25519PrivateKey
) -> None:
    payload = b"archive bytes"
    values = manifest_values(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    content = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    release = {
        "tag_name": "v0.5.0",
        "assets": [
            {"name": name, "browser_download_url": name}
            for name in ["update.json", "update.json.sig", values["asset"]]
        ],
    }
    responses = {
        "latest": json.dumps(release).encode(),
        "update.json": content,
        "update.json.sig": base64.b64encode(signing_key.sign(content)),
        values["asset"]: payload,
    }

    class FixtureClient(update.ReleaseClient):
        def chunks(self, url: str):  # type: ignore[no-untyped-def]
            yield responses[url]

    staged = tmp_path / "staged/Scan64.app"
    monkeypatch.setattr(update, "stage_archive", lambda *_: staged)
    controller = update.UpdateController(
        tmp_path,
        "0.4.0",
        tmp_path / "Scan64.app",
        release_url="latest",
        public_key=base64.b64encode(signing_key.public_key().public_bytes_raw()).decode(),
        client=FixtureClient(),
    )
    controller.set_automatic(False)
    controller.check()  # Manual checks still work with automatic checks disabled.
    assert controller.snapshot()["status"] == "ready"
    assert controller.snapshot()["version"] == "0.5.0"
    assert controller._staged == staged
    controller.check()
    assert controller.snapshot()["status"] == "ready"
    controller._set("idle", "Up to date")
    responses["update.json.sig"] = base64.b64encode(bytes(64))
    controller.check()
    assert controller.snapshot()["status"] == "failed"
    assert "signature" in controller.snapshot()["message"]
    controller.stop()


def test_test_seam_rejects_source_execution(tmp_path: Path) -> None:
    from scan64.desktop import run_update_test

    with pytest.raises(ValueError, match="frozen"):
        run_update_test(tmp_path / "test.json")


def test_test_seam_rejects_real_locations(tmp_path: Path) -> None:
    from scan64.desktop import _temporary_path

    with pytest.raises(ValueError, match="/tmp"):
        _temporary_path(Path("/Applications/Scan64.app"))
    with pytest.raises(ValueError, match="/tmp"):
        _temporary_path(Path.home() / "Library/Application Support/Scan64")


def test_restart_is_one_shot_and_retryable_on_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = update.UpdateController(tmp_path, "0.4.0", tmp_path / "Scan64.app")
    controller._manifest = update.Manifest("0.5.0", "archive.zip", "a" * 64, 1)
    controller._staged = tmp_path / "staged/Scan64.app"
    controller._set("ready", "Ready", "0.5.0")
    monkeypatch.setattr(update, "unavailable_reason", lambda _: None)
    calls: list[str] = []

    def fail_launch(plan: update.InstallPlan) -> None:
        assert controller.snapshot()["status"] == "installing"
        raise OSError("Cannot start helper")

    monkeypatch.setattr(update.InstallPlan, "launch", fail_launch)
    with pytest.raises(OSError, match="helper"):
        controller.restart()
    assert controller.snapshot()["status"] == "ready"
    monkeypatch.setattr(update.InstallPlan, "launch", lambda _: calls.append("launched"))
    controller.restart()
    assert controller.snapshot()["status"] == "installing"
    with pytest.raises(update.UpdateError, match="verified"):
        controller.restart()
    assert calls == ["launched"]


class StalledClient(update.ReleaseClient):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def fetch(self, url: str, cap: int) -> bytes:
        self.entered.set()
        self.release.wait(30)
        raise update.UpdateError("stalled")


def test_stop_does_not_wait_for_a_stalled_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(update, "STOP_TIMEOUT_SECONDS", 0.2)
    client = StalledClient()
    controller = update.UpdateController(tmp_path, "0.4.0", tmp_path / "Scan64.app", client=client)
    controller.check_background()
    assert client.entered.wait(5)
    started = time.monotonic()
    controller.stop()
    assert time.monotonic() - started < 2
    client.release.set()
