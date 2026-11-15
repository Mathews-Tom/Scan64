"""Verify Tauri artifacts on a secret-free runner before creating release assets.

Uses minisign 0.11 (Ubuntu 24.04 package 0.11-1), including its authenticated
trusted-comment check. Never imports or executes anything from the artifacts.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
SHELL = Path("apps/scan64-desktop/src-tauri")
REPOSITORY = "Mathews-Tom/Scan64"
# One signed updater artifact per platform; macOS/Linux also have manual installers.
LAYOUT = {
    "macos-15": ("darwin-aarch64", "*.app.tar.gz", ("*.dmg",)),
    "windows-latest": ("windows-x86_64", "*-setup.exe", ()),
    "ubuntu-22.04": ("linux-x86_64", "*.AppImage", ("*.deb",)),
}


def release_identity(root: Path, event: str, tag: str) -> tuple[str, str]:
    config = json.loads((root / SHELL / "tauri.conf.json").read_text(encoding="utf-8"))
    version = config["version"]
    if not isinstance(version, str) or not re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
        r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?",
        version,
    ):
        raise ValueError("Invalid Tauri version")
    if event == "release":
        python = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        crate = tomllib.loads((root / SHELL / "Cargo.toml").read_text(encoding="utf-8"))
        lock = tomllib.loads((root / SHELL / "Cargo.lock").read_text(encoding="utf-8"))
        locked = [p["version"] for p in lock["package"] if p["name"] == "scan64-desktop"]
        if (
            tag != f"v{version}"
            or python["project"]["version"] != version
            or crate["package"]["version"] != version
            or locked != [version]
        ):
            raise ValueError("Release tag, Python, Tauri, crate and lock versions must match")
    elif event in {"pull_request", "workflow_dispatch"}:
        # Before release preparation, shell 0.0.0 and Python may intentionally differ.
        # Do not modify package versions, create a tag or publish anything for a dry run.
        tag = f"v{version}"
    else:
        raise ValueError("Unsupported release workflow event")
    return version, tag


def artifact_inventory(source: Path) -> tuple[list[Path], dict[str, Path]]:
    files = []
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError("Release artifacts must not contain symlinks")
        if path.is_file():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", path.name):
                raise ValueError("Unsafe release asset filename")
            files.append(path)
    selected: list[Path] = []
    platforms = {}
    for runner, (platform, bundle_pattern, installers) in LAYOUT.items():
        directory = source / f"release-bundles-{runner}"
        for pattern in (bundle_pattern, *installers):
            matches = [path for path in directory.rglob(pattern) if path.is_file()]
            if len(matches) != 1:
                raise ValueError(f"Expected exactly one {platform} {pattern}")
            asset = matches[0]
            selected.append(asset)
            if pattern == bundle_pattern:
                signature = asset.with_name(asset.name + ".sig")
                if not signature.is_file():
                    raise ValueError(f"Missing signature for {asset.name}")
                selected.append(signature)
                platforms[platform] = asset
    if set(selected) != set(files) or len({path.name for path in files}) != len(files):
        raise ValueError("Unexpected, duplicate or unmatched release artifacts")
    return selected, platforms


def verify_signature(asset: Path, public_key: str, version: str) -> str:
    encoded = asset.with_name(asset.name + ".sig").read_text(encoding="utf-8").strip()
    signature = base64.b64decode(encoded, validate=True).decode("utf-8")
    key = base64.b64decode(public_key, validate=True).decode("utf-8")
    with tempfile.TemporaryDirectory(prefix="scan64-public-verification-") as temporary:
        directory = Path(temporary)
        (directory / "public.key").write_text(key, encoding="utf-8")
        (directory / "artifact.minisig").write_text(signature, encoding="utf-8")
        result = subprocess.run(
            [
                "minisign",
                "-Vm",
                str(asset.resolve()),
                "-p",
                str(directory / "public.key"),
                "-x",
                str(directory / "artifact.minisig"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    # Only inspect the trusted comment AFTER minisign checks both signatures.
    lines = signature.splitlines()
    if len(lines) != 4 or not lines[2].startswith("trusted comment: "):
        raise ValueError("Invalid minisign trusted comment")
    versions = [
        field.removeprefix("version:")
        for field in lines[2].removeprefix("trusted comment: ").split("\t")
        if field.startswith("version:")
    ]
    if versions != [version]:
        raise ValueError(f"Signature does not authenticate version {version}: {asset.name}")
    print(result.stdout.strip())
    print(f"Verified {asset.name}; authenticated version {version}", flush=True)
    return encoded


def prepare(source: Path, output: Path) -> dict[str, object]:
    version, tag = release_identity(ROOT, os.environ["RELEASE_EVENT"], os.getenv("RELEASE_TAG", ""))
    if os.environ["RELEASE_REPOSITORY"] != REPOSITORY:
        raise ValueError("Unexpected release repository")
    files, bundles = artifact_inventory(source)
    config = json.loads((ROOT / SHELL / "tauri.conf.json").read_text(encoding="utf-8"))
    public_key = config["plugins"]["updater"]["pubkey"]
    platforms = {}
    for platform, asset in bundles.items():
        signature = verify_signature(asset, public_key, version)
        platforms[platform] = {
            "url": f"https://github.com/{REPOSITORY}/releases/download/{quote(tag, safe='')}/"
            f"{quote(asset.name, safe='')}",
            "signature": signature,
        }
    published = os.getenv("RELEASE_PUBLISHED_AT")
    if os.environ["RELEASE_EVENT"] == "release" and not published:
        raise ValueError("Published release timestamp is required")
    pub_date = published or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
    manifest: dict[str, object] = {
        "version": version,
        "notes": f"https://github.com/{REPOSITORY}/releases/tag/{quote(tag, safe='')}",
        "pub_date": pub_date,
        "platforms": platforms,
    }
    # No output is admitted until ALL signatures, versions and inventory checks pass.
    output.mkdir(exist_ok=False)
    for path in files:
        shutil.copyfile(path, output / path.name)
    content = json.dumps(manifest, indent=2) + "\n"
    (output / "latest.json").write_text(content, encoding="utf-8")
    print(f"Event: {os.environ['RELEASE_EVENT']}; version: {version}; tag: {tag}")
    print(content)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as report:
            report.write(
                f"### Verified release assets ({os.environ['RELEASE_EVENT']})\n\n"
                "All three updater signatures and authenticated versions verified against "
                "the committed production public key.\n\n"
                f"```json\n{content}```\n"
            )
    return manifest


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
