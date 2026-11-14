"""Exercise the operator CLI and release asset wire format with disposable keys."""

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sign-release.py"


def run_cli(*args: str | Path, seed: str | None = None) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items() if key != "SCAN64_RELEASE_SIGNING_KEY"
    }
    if seed is not None:
        environment["SCAN64_RELEASE_SIGNING_KEY"] = seed
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    archive = tmp_path / "Scan64-1.2.3-macos-arm64.zip"
    archive.write_bytes(b"disposable release bytes")
    result = run_cli("manifest", archive, "1.2.3")
    assert result.returncode == 0, result.stderr
    return tmp_path / "update.json"


@pytest.fixture
def seed() -> str:
    return base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode("ascii")


def public_key(seed: str) -> str:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed))
    return base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")


def test_manifest_is_exact_canonical_contract(manifest: Path) -> None:
    content = b"disposable release bytes"
    expected = {
        "asset": "Scan64-1.2.3-macos-arm64.zip",
        "format": 1,
        "platform": "macos-arm64",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "version": "1.2.3",
    }
    assert manifest.read_bytes() == json.dumps(
        expected, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    custom = manifest.parent / "custom.json"
    assert (
        run_cli(
            "manifest", manifest.parent / expected["asset"], "1.2.3", "--output", custom
        ).returncode
        == 0
    )
    assert custom.read_bytes() == manifest.read_bytes()


@pytest.mark.parametrize("version", ["v1.2.3", "../1.2.3", "", "1.2", "1.2.3\n"])
def test_reject_invalid_version(tmp_path: Path, version: str) -> None:
    archive = tmp_path / "Scan64-1.2.3-macos-arm64.zip"
    archive.write_bytes(b"test")
    assert run_cli("manifest", archive, version).returncode != 0
    assert not (tmp_path / "update.json").exists()


def test_reject_wrong_asset_name(tmp_path: Path) -> None:
    archive = tmp_path / "wrong.zip"
    archive.write_bytes(b"test")
    assert run_cli("manifest", archive, "1.2.3").returncode != 0


def test_sign_verify_file_and_base64(manifest: Path, seed: str) -> None:
    signed = run_cli("sign", manifest, seed=seed)
    assert signed.returncode == 0, signed.stderr
    assert signed.stdout == signed.stderr == ""
    signature = Path(f"{manifest}.sig")
    assert len(base64.b64decode(signature.read_text(), validate=False)) == 64
    assert len(signature.read_text().splitlines()) == 1
    key_file = manifest.parent / "public.key"
    key_file.write_text(public_key(seed) + "\n")
    for key in (str(key_file), public_key(seed)):
        result = run_cli("verify", manifest, "--public-key", key)
        assert result.returncode == 0, result.stderr
    custom = manifest.parent / "custom.sig"
    assert run_cli("sign", manifest, "--output", custom, seed=seed).returncode == 0
    assert custom.read_bytes() == signature.read_bytes()
    assert (
        run_cli("verify", manifest, "--signature", custom, "--public-key", key_file).returncode == 0
    )
    assert seed not in signed.stdout + signed.stderr


def test_reject_tampered_manifest(manifest: Path, seed: str) -> None:
    assert run_cli("sign", manifest, seed=seed).returncode == 0
    manifest.write_bytes(manifest.read_bytes() + b" ")
    result = run_cli("verify", manifest, "--public-key", public_key(seed))
    assert result.returncode == 1
    assert "verification failed" in result.stderr


def test_reject_wrong_public_key(manifest: Path, seed: str) -> None:
    assert run_cli("sign", manifest, seed=seed).returncode == 0
    wrong = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    assert (
        run_cli(
            "verify", manifest, "--public-key", base64.b64encode(wrong).decode("ascii")
        ).returncode
        == 1
    )


@pytest.mark.parametrize("contents", ["not base64!", "YQ==", ""])
def test_reject_bad_signature(manifest: Path, seed: str, contents: str) -> None:
    Path(f"{manifest}.sig").write_text(contents)
    assert run_cli("verify", manifest, "--public-key", public_key(seed)).returncode == 1


@pytest.mark.parametrize("seed", [None, "", "sensitive-invalid-seed!", "YQ=="])
def test_signing_errors_never_disclose_seed(manifest: Path, seed: str | None) -> None:
    result = run_cli("sign", manifest, seed=seed)
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    if seed:
        assert seed not in result.stderr
    assert not Path(f"{manifest}.sig").exists()


def test_keygen_prints_seed_once_and_only_writes_public_file(
    tmp_path: Path, manifest: Path
) -> None:
    destination = tmp_path / "public.key"
    result = run_cli("keygen", "--public-out", destination)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 1
    seed = result.stdout.strip()
    assert len(base64.b64decode(seed, validate=True)) == 32
    assert destination.read_text().strip() == public_key(seed)
    before = {path.name for path in tmp_path.iterdir()}
    retry = run_cli("keygen", "--public-out", destination)
    assert retry.returncode == 1
    assert retry.stdout == ""
    assert {path.name for path in tmp_path.iterdir()} == before
    assert run_cli("sign", manifest, seed=seed).returncode == 0
    assert run_cli("verify", manifest, "--public-key", destination).returncode == 0


def test_missing_inputs_fail_cleanly(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    result = run_cli("verify", missing, "--public-key", "bad-key")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
