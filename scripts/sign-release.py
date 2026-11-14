#!/usr/bin/env python3
"""Create and authenticate Scan64 update manifests (not Apple code signing)."""

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def decode_base64(value: str, length: int) -> bytes:
    """Reject malformed key material without including it in an error message."""
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid base64 key or signature") from error
    if len(raw) != length:
        raise ValueError(f"Expected {length} decoded bytes")
    return raw


def signing_key() -> Ed25519PrivateKey:
    seed = os.environ.get("SCAN64_RELEASE_SIGNING_KEY", "")
    if not seed:
        raise ValueError("SCAN64_RELEASE_SIGNING_KEY is required")
    return Ed25519PrivateKey.from_private_bytes(decode_base64(seed, 32))


def write_manifest(archive: Path, version: str, output: Path) -> None:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.+-]*", version):
        raise ValueError("Version must be a package version without a leading v")
    if archive.name != f"Scan64-{version}-macos-arm64.zip":
        raise ValueError("Archive filename must match the version and macos-arm64 platform")
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        size = source.tell()
    manifest = {
        "asset": archive.name,
        "format": 1,
        "platform": "macos-arm64",
        "sha256": digest,
        "size": size,
        "version": version,
    }
    output.write_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def sign_manifest(manifest: Path, output: Path) -> None:
    signature = signing_key().sign(manifest.read_bytes())
    output.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")


def verify_manifest(manifest: Path, signature: Path, public_key: str) -> None:
    # A 32-byte base64 key has no path separator ambiguity when explicitly passed.
    try:
        raw = decode_base64(public_key, 32)
    except ValueError:
        raw = decode_base64(Path(public_key).read_text(encoding="ascii"), 32)
    key = Ed25519PublicKey.from_public_bytes(raw)
    key.verify(decode_base64(signature.read_text(encoding="ascii"), 64), manifest.read_bytes())


def generate_key(public_out: Path) -> None:
    key = Ed25519PrivateKey.generate()
    # Never overwrite a trust anchor or persist the private seed to disk.
    with public_out.open("x", encoding="ascii") as output:
        output.write(base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii") + "\n")
    print(base64.b64encode(key.private_bytes_raw()).decode("ascii"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser(
        "manifest", help="Write a canonical update.json for a release zip"
    )
    manifest.add_argument("archive", type=Path)
    manifest.add_argument("version", help="Package version, without a leading v")
    manifest.add_argument("--output", type=Path, help="Default: update.json next to the archive")
    sign = commands.add_parser("sign", help="Sign exact manifest bytes using the environment seed")
    sign.add_argument("manifest", type=Path)
    sign.add_argument("--output", type=Path, help="Default: <manifest>.sig")
    verify = commands.add_parser("verify", help="Verify exact bytes; exit nonzero on any failure")
    verify.add_argument("manifest", type=Path)
    verify.add_argument("--signature", type=Path, help="Default: <manifest>.sig")
    verify.add_argument(
        "--public-key", required=True, help="Public-key file path or base64 raw key"
    )
    keygen = commands.add_parser(
        "keygen", help="Print private seed ONCE; pipe directly to a secret store"
    )
    keygen.add_argument("--public-out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "manifest":
            write_manifest(
                args.archive, args.version, args.output or args.archive.parent / "update.json"
            )
        elif args.command == "sign":
            sign_manifest(args.manifest, args.output or Path(f"{args.manifest}.sig"))
        elif args.command == "verify":
            verify_manifest(
                args.manifest, args.signature or Path(f"{args.manifest}.sig"), args.public_key
            )
        else:
            generate_key(args.public_out)
    except InvalidSignature:
        print("Release signature verification failed", file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError):
        # Paths and decoding errors can contain caller input; keep diagnostics secret-safe.
        print(
            "Release signing failed: check input files, key encoding and arguments", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
