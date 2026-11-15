"""Fail closed on incomplete artifacts, signature failures and mismatched versions."""

from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_verify", Path(__file__).with_name("verify.py")
)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def package(root: Path, version: str = "0.7.0") -> None:
    shell = root / verify.SHELL
    shell.mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    (shell / "Cargo.toml").write_text(f'[package]\nversion = "{version}"\n')
    (shell / "Cargo.lock").write_text(
        f'[[package]]\nname = "scan64-desktop"\nversion = "{version}"\n'
    )
    (shell / "tauri.conf.json").write_text(
        json.dumps({"version": version, "plugins": {"updater": {"pubkey": "public-only"}}})
    )


def artifacts(root: Path) -> Path:
    source = root / "input"
    for runner, (_, pattern, installers) in verify.LAYOUT.items():
        folder = source / f"release-bundles-{runner}" / "bundle"
        folder.mkdir(parents=True)
        for item in (pattern, *installers):
            asset = folder / item.replace("*", "Scan64_0.7.0")
            asset.write_bytes(b"bundle")
            if item == pattern:
                asset.with_name(asset.name + ".sig").write_text("public signature")
    return source


def test_release_versions_and_tag_must_all_match(tmp_path: Path) -> None:
    package(tmp_path)
    assert verify.release_identity(tmp_path, "release", "v0.7.0") == ("0.7.0", "v0.7.0")
    with pytest.raises(ValueError, match="must match"):
        verify.release_identity(tmp_path, "release", "v0.8.0")
    for relative, content in (
        (Path("pyproject.toml"), '[project]\nversion = "0.6.1"\n'),
        (verify.SHELL / "Cargo.toml", '[package]\nversion = "0.0.0"\n'),
        (
            verify.SHELL / "Cargo.lock",
            '[[package]]\nname = "scan64-desktop"\nversion = "0.0.0"\n',
        ),
    ):
        path = tmp_path / relative
        previous = path.read_text()
        path.write_text(content)
        with pytest.raises(ValueError, match="must match"):
            verify.release_identity(tmp_path, "release", "v0.7.0")
        path.write_text(previous)


def test_dry_run_uses_shell_version_without_mutating_sources(tmp_path: Path) -> None:
    package(tmp_path, "0.0.0")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.6.1"\n')
    for event in ("pull_request", "workflow_dispatch"):
        assert verify.release_identity(tmp_path, event, "") == ("0.0.0", "v0.0.0")
    with pytest.raises(ValueError, match="Unsupported"):
        verify.release_identity(tmp_path, "push", "")


@pytest.mark.parametrize(
    "damage", ["signature", "bundle", "installer", "extra", "duplicate", "symlink"]
)
def test_inventory_rejects_every_incomplete_or_unexpected_set(tmp_path: Path, damage: str) -> None:
    source = artifacts(tmp_path)
    bundle = next(source.rglob("*.app.tar.gz"))
    if damage == "signature":
        bundle.with_name(bundle.name + ".sig").unlink()
    elif damage == "bundle":
        bundle.unlink()
    elif damage == "installer":
        next(source.rglob("*.deb")).unlink()
    elif damage == "extra":
        (source / "unmatched.sig").write_text("extra")
    elif damage == "duplicate":
        (bundle.parent / "Other.app.tar.gz").write_bytes(b"duplicate")
    else:
        (source / "linked").symlink_to(bundle)
    with pytest.raises(ValueError):
        verify.artifact_inventory(source)


def test_inventory_requires_all_three_signed_platforms_and_installers(tmp_path: Path) -> None:
    files, platforms = verify.artifact_inventory(artifacts(tmp_path))
    assert len(files) == 8
    assert set(platforms) == {"darwin-aarch64", "windows-x86_64", "linux-x86_64"}


def encoded_signature(comment: str) -> str:
    return base64.b64encode(
        f"untrusted comment: fixture\nc2ln\ntrusted comment: {comment}\nZ2xvYmFs\n".encode()
    ).decode()


def test_signature_verification_checks_crypto_before_trusting_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = tmp_path / "app.AppImage"
    asset.write_bytes(b"bundle")
    signature = encoded_signature("timestamp:1\tfile:app.AppImage\tversion:0.7.0")
    asset.with_name(asset.name + ".sig").write_text(signature)
    key = base64.b64encode(b"public key fixture").decode()
    run = Mock(return_value=subprocess.CompletedProcess([], 0, "Signature verified", ""))
    monkeypatch.setattr(verify.subprocess, "run", run)
    assert verify.verify_signature(asset, key, "0.7.0") == signature
    assert run.call_args.args[0][:2] == ["minisign", "-Vm"]
    assert run.call_args.kwargs["check"] is True
    run.side_effect = subprocess.CalledProcessError(1, "minisign")
    with pytest.raises(subprocess.CalledProcessError):
        verify.verify_signature(asset, key, "0.7.0")


@pytest.mark.parametrize(
    "comment", ["timestamp:1", "version:0.6.1", "version:0.7.0\tversion:0.7.0"]
)
def test_missing_wrong_or_ambiguous_authenticated_versions_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, comment: str
) -> None:
    asset = tmp_path / "app.AppImage"
    asset.write_bytes(b"bundle")
    asset.with_name(asset.name + ".sig").write_text(encoded_signature(comment))
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        Mock(return_value=subprocess.CompletedProcess([], 0, "Signature verified", "")),
    )
    with pytest.raises(ValueError, match="authenticate version"):
        verify.verify_signature(asset, base64.b64encode(b"public").decode(), "0.7.0")


def test_prepare_outputs_only_verified_assets_with_inline_signatures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package(tmp_path)
    source = artifacts(tmp_path)
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    monkeypatch.setenv("RELEASE_EVENT", "release")
    monkeypatch.setenv("RELEASE_TAG", "v0.7.0")
    monkeypatch.setenv("RELEASE_REPOSITORY", verify.REPOSITORY)
    monkeypatch.setenv("RELEASE_PUBLISHED_AT", "2026-11-15T00:00:00Z")
    verifier = Mock(return_value="verified inline signature")
    monkeypatch.setattr(verify, "verify_signature", verifier)
    output = tmp_path / "output"
    manifest = verify.prepare(source, output)
    assert verifier.call_count == 3
    assert len(list(output.iterdir())) == 9
    assert json.loads((output / "latest.json").read_text()) == manifest
    assert manifest["notes"].endswith("/releases/tag/v0.7.0")
    for platform in manifest["platforms"].values():
        assert platform["signature"] == "verified inline signature"
        assert "/releases/download/v0.7.0/" in platform["url"]
    verifier.side_effect = subprocess.CalledProcessError(1, "minisign")
    rejected = tmp_path / "rejected"
    with pytest.raises(subprocess.CalledProcessError):
        verify.prepare(source, rejected)
    assert not rejected.exists()


def test_production_trust_anchor_is_owner_supplied_public_key() -> None:
    config = json.loads((verify.ROOT / verify.SHELL / "tauri.conf.json").read_text())
    decoded = base64.b64decode(config["plugins"]["updater"]["pubkey"]).decode().splitlines()
    raw = base64.b64decode(decoded[1])
    assert raw[:2] == b"Ed"
    assert raw[2:10][::-1].hex().upper() == "058C7236D5853CF5"
