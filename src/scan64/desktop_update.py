"""Signed, opt-in-on-restart macOS updates; no production trust overrides.

Only the desktop entry point constructs a controller. Test dependencies are explicit
constructor arguments, never environment variables consulted by the shipped app.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import threading
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

LATEST_URL = "https://api.github.com/repos/Mathews-Tom/Scan64/releases/latest"
RELEASE_URL = "https://github.com/Mathews-Tom/Scan64/releases/latest"
STOP_TIMEOUT_SECONDS = 2.0
LOGGER = logging.getLogger(__name__)


class UpdateError(ValueError):
    """An update cannot safely be used."""


@dataclass(frozen=True)
class Manifest:
    version: str
    asset: str
    sha256: str
    size: int


def decode_base64(value: str, length: int) -> bytes:
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (ValueError, binascii.Error) as error:
        raise UpdateError("Invalid base64 key or signature") from error
    if len(raw) != length:
        raise UpdateError(f"Expected {length} decoded bytes")
    return raw


def verify_manifest(
    content: bytes, signature: bytes, public_key: str, tag: str, current: str
) -> Manifest | None:
    """Authenticate exact bytes before interpreting any download instructions."""
    try:
        Ed25519PublicKey.from_public_bytes(decode_base64(public_key, 32)).verify(
            decode_base64(signature.decode("ascii"), 64), content
        )
    except (InvalidSignature, UnicodeError) as error:
        raise UpdateError("Release signature verification failed") from error
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError) as error:
        raise UpdateError("Invalid update manifest JSON") from error
    if not isinstance(value, dict) or set(value) != {
        "asset",
        "format",
        "platform",
        "sha256",
        "size",
        "version",
    }:
        raise UpdateError("Invalid update manifest fields")
    if type(value["format"]) is not int or value["format"] != 1:
        raise UpdateError("Unsupported manifest format")
    if value["platform"] != "macos-arm64":
        raise UpdateError("Unsupported update platform")
    version = value["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*", version
    ):
        raise UpdateError("Invalid update version")
    if version != tag.removeprefix("v"):
        raise UpdateError("Release tag and manifest version differ")
    if value["asset"] != f"Scan64-{version}-macos-arm64.zip":
        raise UpdateError("Update asset name does not match version")
    if not isinstance(value["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", value["sha256"]):
        raise UpdateError("Invalid archive digest")
    if type(value["size"]) is not int or value["size"] <= 0:
        raise UpdateError("Invalid archive size")
    try:
        newer = Version(version) > Version(current)
    except InvalidVersion as error:
        raise UpdateError("Invalid package version") from error
    if not newer:
        LOGGER.info("Ignoring release that is not newer than the running version")
        return None
    return Manifest(version, value["asset"], value["sha256"], value["size"])


def bundle_version(bundle: Path) -> str:
    with (bundle / "Contents/Info.plist").open("rb") as source:
        value = plistlib.load(source).get("CFBundleShortVersionString")
    if not isinstance(value, str):
        raise UpdateError("Bundle has no version")
    return value


def app_bundle() -> Path | None:
    return next(
        (path for path in Path(sys.executable).absolute().parents if path.suffix == ".app"), None
    )


def unavailable_reason(
    bundle: Path | None,
    *,
    frozen: bool | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> str | None:
    if not (getattr(sys, "frozen", False) if frozen is None else frozen):
        return "Updates require the packaged app"
    if (system or platform.system()) != "Darwin" or (machine or platform.machine()) != "arm64":
        return "Updates require macOS on Apple silicon"
    if bundle is None:
        return "Application bundle not found"
    if "/AppTranslocation/" in str(bundle):
        return "Move Scan64 to Applications before updating"
    if not os.access(bundle.parent, os.W_OK):
        return "The application folder is not writable"
    return None


def load_settings(data: Path) -> bool:
    try:
        value = json.loads((data / "settings.json").read_text())
        if isinstance(value, dict) and type(value.get("automatic_updates")) is bool:
            return bool(value["automatic_updates"])
    except (OSError, ValueError):
        pass
    return True


def save_settings(data: Path, automatic: bool) -> None:
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = data / "settings.json.tmp"
    temporary.write_text(json.dumps({"automatic_updates": automatic}) + "\n")
    temporary.replace(data / "settings.json")


class ReleaseClient:
    """Bounded metadata reads and streaming archive reads with HTTPS redirects only."""

    def __init__(self, *, test_loopback: bool = False) -> None:
        self.test_loopback = test_loopback

    def validate_request(self, request: httpx.Request) -> None:
        if request.url.scheme == "https":
            return
        if (
            self.test_loopback
            and request.url.scheme == "http"
            and request.url.host in {"127.0.0.1", "localhost", "::1"}
        ):
            return
        raise UpdateError("Update downloads require HTTPS")

    def client(self) -> httpx.Client:
        return httpx.Client(
            timeout=20,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "Scan64-desktop-updater", "Accept": "application/octet-stream"},
            event_hooks={"request": [self.validate_request]},
        )

    def chunks(self, url: str) -> Iterator[bytes]:
        with self.client() as client, client.stream("GET", url) as response:
            response.raise_for_status()
            yield from response.iter_bytes(64 * 1024)

    def fetch(self, url: str, cap: int) -> bytes:
        result = bytearray()
        for chunk in self.chunks(url):
            if len(result) + len(chunk) > cap:
                raise UpdateError("Release metadata exceeds its size limit")
            result.extend(chunk)
        return bytes(result)


def release_assets(value: Any) -> tuple[str, dict[str, str]]:
    if not isinstance(value, dict) or not isinstance(value.get("tag_name"), str):
        raise UpdateError("Invalid release response")
    entries = value.get("assets")
    if not isinstance(entries, list):
        raise UpdateError("Release has no assets")
    assets: dict[str, str] = {}
    for item in entries:
        if not isinstance(item, dict):
            raise UpdateError("Invalid release asset")
        name, url = item.get("name"), item.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str) or name in assets:
            raise UpdateError("Invalid or duplicate release asset")
        assets[name] = url
    return value["tag_name"], assets


def download_archive(
    client: ReleaseClient,
    url: str,
    manifest: Manifest,
    directory: Path,
    progress: Callable[[int], None],
    cancelled: threading.Event,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = directory / manifest.asset
    temporary = destination.with_suffix(".part")
    count = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            for chunk in client.chunks(url):
                if cancelled.is_set():
                    raise UpdateError("Update cancelled")
                count += len(chunk)
                if count > manifest.size:
                    raise UpdateError("Archive exceeds signed size")
                output.write(chunk)
                digest.update(chunk)
                progress(count * 100 // manifest.size)
            output.flush()
            os.fsync(output.fileno())
        if count != manifest.size:
            raise UpdateError("Archive size does not match manifest")
        if digest.hexdigest() != manifest.sha256:
            raise UpdateError("Archive digest does not match manifest")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def validate_archive(archive: Path) -> None:
    """Keep ditto extraction within staging, including framework symlinks."""
    with zipfile.ZipFile(archive) as source:
        symlinks = {
            PurePosixPath(entry.filename.casefold())
            for entry in source.infolist()
            if stat.S_ISLNK(entry.external_attr >> 16)
        }
        for entry in source.infolist():
            path = PurePosixPath(entry.filename)
            # ditto's resource-fork entries live in __MACOSX beside the app.
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or path.parts[0] not in {"Scan64.app", "__MACOSX"}
            ):
                raise UpdateError("Unsafe archive path")
            if any(
                parent in symlinks for parent in PurePosixPath(entry.filename.casefold()).parents
            ):
                raise UpdateError("Archive entry traverses a symlink")
            if stat.S_ISLNK(entry.external_attr >> 16):
                if len(path.parts) == 1:
                    raise UpdateError("Unsafe archive root symlink")
                if entry.file_size > 4096:
                    raise UpdateError("Unsafe archive symlink")
                target = PurePosixPath(source.read(entry).decode("utf-8"))
                if target.is_absolute():
                    raise UpdateError("Unsafe archive symlink")
                depth = len(path.parent.parts)
                for part in target.parts:
                    depth += -1 if part == ".." else 1
                    if depth < 1:
                        raise UpdateError("Archive symlink escapes app")


def stage_archive(archive: Path, manifest: Manifest, directory: Path) -> Path:
    validate_archive(archive)
    staging = directory / "staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, mode=0o700)
    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(staging)], check=True)
    bundle = staging / "Scan64.app"
    if bundle.is_symlink() or bundle_version(bundle) != manifest.version:
        raise UpdateError("Staged bundle version does not match manifest")
    return bundle


# Static shell source. All paths/versions are separate argv, quoted at every use;
# there is no eval, shell interpolation of caller input, or writable PATH lookup.
INSTALL_HELPER = r"""#!/bin/bash
set -eu
umask 077
pid="$1"; bundle="$2"; staged="$3"; data="$4"; old="$5"; new="$6"
shift 6
previous="${bundle}.previous"
moved=0
exec >> "$data/update.log" 2>&1
rollback() {
    result=$?
    trap - EXIT
    if [ "$result" -ne 0 ]; then
        printf 'Update failed (%s); restoring previous app\n' "$result"
        if [ "$moved" = 1 ]; then
            /bin/rm -rf -- "$bundle"
            /bin/mv -- "$previous" "$bundle" || exit 1
        fi
        "$@" || true
    fi
    exit "$result"
}
trap 'rollback "$@"' EXIT
while /bin/kill -0 "$pid" 2>/dev/null; do /bin/sleep 0.2; done
/bin/mkdir -p -- "$data/backups"
stamp=$(/bin/date -u '+%Y%m%dT%H%M%SZ')
backup="$data/backups/scan64-${old}-${stamp}.db"
if [ -f "$data/scan64.db" ]; then
    /bin/cp -p -- "$data/scan64.db" "$backup"
    for suffix in -wal -shm; do
        if [ -f "$data/scan64.db$suffix" ]; then
            /bin/cp -p -- "$data/scan64.db$suffix" "$backup$suffix"
        fi
    done
fi
staged_version=$(/usr/libexec/PlistBuddy \
    -c 'Print :CFBundleShortVersionString' "$staged/Contents/Info.plist")
[ "$staged_version" = "$new" ]
/bin/rm -rf -- "$previous"
/bin/mv -- "$bundle" "$previous"
moved=1
/usr/bin/ditto "$staged" "$bundle"
installed_version=$(/usr/libexec/PlistBuddy \
    -c 'Print :CFBundleShortVersionString' "$bundle/Contents/Info.plist")
[ "$installed_version" = "$new" ]
"$@"
printf 'Installed Scan64 %s (previous %s); backup %s\n' "$new" "$old" "$backup"
"""


@dataclass(frozen=True)
class InstallPlan:
    bundle: Path
    staged: Path
    data: Path
    old_version: str
    new_version: str
    pid: int
    relaunch_command: Sequence[str] | None = None

    def command(self) -> list[str]:
        for path in (self.bundle, self.staged, self.data):
            if not path.is_absolute():
                raise UpdateError("Install paths must be absolute")
        for version in (self.old_version, self.new_version):
            if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*", version):
                raise UpdateError("Invalid install version")
        helper = self.data / "updates" / "install.sh"
        helper.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        helper.write_text(INSTALL_HELPER)
        helper.chmod(0o700)
        return [
            "/bin/bash",
            str(helper),
            str(self.pid),
            str(self.bundle),
            str(self.staged),
            str(self.data),
            self.old_version,
            self.new_version,
            *(self.relaunch_command or ["/usr/bin/open", "-n", str(self.bundle)]),
        ]

    def launch(self) -> None:
        with (self.data / "update.log").open("a") as log:
            subprocess.Popen(
                self.command(),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )


class UpdateController:
    def __init__(
        self,
        data: Path,
        current_version: str,
        bundle: Path | None,
        *,
        public_key: str | None = None,
        release_url: str = LATEST_URL,
        client: ReleaseClient | None = None,
        relaunch_command: Sequence[str] | None = None,
        disabled_reason: str | None = None,
    ) -> None:
        self.data = data
        self.current_version = current_version
        self.bundle = bundle
        self.public_key = (
            public_key or Path(__file__).with_name("release_signing_key.pub").read_text()
        )
        self.release_url = release_url
        self.client = client or ReleaseClient()
        self.relaunch_command = relaunch_command
        self.disabled_reason = disabled_reason
        self._lock = threading.Lock()
        self._check_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._scheduler: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._manifest: Manifest | None = None
        self._staged: Path | None = None
        self._state: dict[str, Any] = {
            "status": "unavailable" if disabled_reason else "idle",
            "message": f"Updates unavailable: {disabled_reason}"
            if disabled_reason
            else "Up to date",
            "version": None,
            "available": disabled_reason is None,
            "automatic_updates": load_settings(data),
            "release_url": RELEASE_URL,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, status: str, message: str, version: str | None = None) -> None:
        with self._lock:
            self._state.update(status=status, message=message, version=version)

    def set_automatic(self, automatic: bool) -> None:
        with self._lock:
            save_settings(self.data, automatic)
            self._state["automatic_updates"] = automatic
        self._wake.set()

    def check(self) -> None:
        if self.disabled_reason or self._stop.is_set() or not self._check_lock.acquire(False):
            return
        try:
            if self.snapshot()["status"] == "ready":
                return
            self._set("checking", "Checking")
            tag, assets = release_assets(
                json.loads(self.client.fetch(self.release_url, 2 * 1024 * 1024))
            )
            if "update.json" not in assets or "update.json.sig" not in assets:
                raise UpdateError("Release is missing signed update metadata")
            manifest = verify_manifest(
                self.client.fetch(assets["update.json"], 65536),
                self.client.fetch(assets["update.json.sig"], 1024),
                self.public_key,
                tag,
                self.current_version,
            )
            if manifest is None:
                self._set("idle", "Up to date")
                return
            if manifest.asset not in assets:
                raise UpdateError("Release is missing its signed archive")
            directory = self.data / "updates" / manifest.version
            self._set("downloading", "Downloading 0%", manifest.version)
            archive = download_archive(
                self.client,
                assets[manifest.asset],
                manifest,
                directory,
                lambda percent: self._set(
                    "downloading", f"Downloading {percent}%", manifest.version
                ),
                self._stop,
            )
            staged = stage_archive(archive, manifest, directory)
            self._manifest, self._staged = manifest, staged
            self._set("ready", "Ready — Restart to update", manifest.version)
        except Exception as error:
            LOGGER.warning("Update rejected: %s", error)
            self._set("failed", f"Update failed: {error}")
        finally:
            self._check_lock.release()

    def check_background(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(target=self.check, name="scan64-update", daemon=True)
            self._worker.start()

    def start(self) -> None:
        if self.disabled_reason:
            return

        def schedule() -> None:
            delay = 5.0
            while not self._stop.is_set():
                self._wake.wait(delay)
                self._wake.clear()
                if self._stop.is_set():
                    return
                if self.snapshot()["automatic_updates"]:
                    self.check_background()
                delay = 24 * 60 * 60

        self._scheduler = threading.Thread(
            target=schedule, name="scan64-update-schedule", daemon=True
        )
        self._scheduler.start()

    def stop(self) -> None:
        # Both threads are daemons and staging lives only in memory, so an
        # unfinished download is simply repeated next launch. Never let a
        # stalled socket freeze the Tk thread during quit.
        self._stop.set()
        self._wake.set()
        for thread in (self._scheduler, self._worker):
            if thread:
                thread.join(timeout=STOP_TIMEOUT_SECONDS)

    def restart(self) -> None:
        with self._lock:
            if (
                self._state["status"] != "ready"
                or not self.bundle
                or not self._manifest
                or not self._staged
                or self.disabled_reason
            ):
                raise UpdateError("No verified update is ready")
            self._state.update(status="installing", message="Restarting to update")
        try:
            reason = unavailable_reason(self.bundle)
            if reason:
                raise UpdateError(reason)
            InstallPlan(
                self.bundle,
                self._staged,
                self.data,
                self.current_version,
                self._manifest.version,
                os.getpid(),
                self.relaunch_command,
            ).launch()
        except Exception:
            self._set("ready", "Ready — Restart to update", self._manifest.version)
            raise


_controller: UpdateController | None = None


def set_update_controller(controller: UpdateController | None) -> None:
    global _controller
    _controller = controller


def get_update_state() -> dict[str, Any]:
    if _controller is None:
        return {
            "status": "unavailable",
            "message": "Updates unavailable: desktop is not running",
            "version": None,
            "automatic_updates": True,
            "available": False,
            "release_url": RELEASE_URL,
        }
    return _controller.snapshot()
