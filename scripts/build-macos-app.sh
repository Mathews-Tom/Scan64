#!/usr/bin/env bash
# Build an unsigned (ad-hoc signed) one-folder Apple silicon app. No release upload.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo 'This build requires macOS on Apple silicon.' >&2; exit 1;
}
for tool in uv pnpm make clang curl ditto shasum; do command -v "$tool" >/dev/null; done
xcode-select -p >/dev/null
uv sync --locked --group build --python-preference only-managed
# A working GUI is a design prerequisite, not an optional background fallback.
uv run --no-sync python - <<'PY'
import tkinter
root = tkinter.Tk()
root.withdraw()
root.update()
print("Bundled Python Tk:", root.tk.call("info", "patchlevel"))
root.destroy()
PY
stage="$PWD/build/macos-inputs"
mkdir -p "$stage/stockfish" "$stage/bin" "$stage/source"
# The install-only managed Python omits notices for statically linked libraries.
# Recover them from its exact standalone full build, verifying GitHub's asset digest.
uv run --no-sync python - "$stage" <<'PY'
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import urllib.request

import zstandard

stage = Path(sys.argv[1])
build = (Path(sys.base_prefix) / "BUILD").read_text().strip()
version = ".".join(str(value) for value in sys.version_info[:3])
name = f"cpython-{version}+{build}-aarch64-apple-darwin-pgo+lto-full.tar.zst"
request = urllib.request.Request(
    f"https://api.github.com/repos/astral-sh/python-build-standalone/releases/tags/{build}",
    headers={"Accept": "application/vnd.github+json"},
)
# Shared CI runner addresses exhaust the anonymous API limit; use a token when given.
token = os.environ.get("GITHUB_TOKEN")
if token:
    request.add_header("Authorization", f"Bearer {token}")
with urllib.request.urlopen(request, timeout=60) as response:
    release = json.load(response)
asset = next(item for item in release["assets"] if item["name"] == name)
expected = asset["digest"].removeprefix("sha256:")
archive_path = stage / "python-full.tar.zst"
subprocess.run(
    ["curl", "--fail", "--location", "--retry", "3",
     asset["browser_download_url"], "--output", str(archive_path)], check=True,
)
with archive_path.open("rb") as source:
    actual = hashlib.file_digest(source, "sha256").hexdigest()
if actual != expected:
    raise SystemExit("Python full archive SHA-256 mismatch")
output = stage / "python-licenses"
output.mkdir(exist_ok=True)
count = 0
with archive_path.open("rb") as source, zstandard.ZstdDecompressor().stream_reader(source) as stream:
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            if member.isfile() and member.name.startswith("python/licenses/"):
                relative = Path(member.name).relative_to("python/licenses")
                if ".." in relative.parts:
                    raise SystemExit("Unsafe Python license archive path")
                content = archive.extractfile(member)
                assert content is not None
                destination = output / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content.read())
                count += 1
if not count:
    raise SystemExit("Python full archive did not include component licenses")
print(f"Python {version}+{build} component licenses: {count}; archive SHA-256 {actual}")
PY
# Read the existing container pins so these distribution paths cannot silently drift.
uv run --no-sync python - "$stage/stockfish" <<'PY'
import pathlib
import re
import subprocess
import sys
pins = pathlib.Path("Dockerfile").read_text()
out = pathlib.Path(sys.argv[1])
for name, url in [
    ("stockfish-sf_18.tar.gz", "https://codeload.github.com/official-stockfish/Stockfish/tar.gz/refs/tags/sf_18"),
    ("nn-c288c895ea92.nnue", "https://tests.stockfishchess.org/api/nn/nn-c288c895ea92.nnue"),
    ("nn-37f18f62d772.nnue", "https://tests.stockfishchess.org/api/nn/nn-37f18f62d772.nnue"),
]:
    match = re.search(r"echo '([0-9a-f]{64})  (?:/out/|src/)" + re.escape(name) + "'", pins)
    if match is None or url not in pins:
        raise SystemExit(f"Dockerfile pin missing: {name}")
    subprocess.run(["curl", "--fail", "--location", "--retry", "3", url, "--output", str(out / name)], check=True)
    subprocess.run(["shasum", "-a", "256", "--check"], input=f"{match[1]}  {out / name}\n", text=True, check=True)
PY
tar -xzf "$stage/stockfish/stockfish-sf_18.tar.gz" -C "$stage/source" --strip-components=1
cp "$stage/stockfish/"*.nnue "$stage/source/src/"
make -C "$stage/source/src" -j2 build ARCH=apple-silicon COMP=clang
cp "$stage/source/src/stockfish" "$stage/bin/stockfish"
cp "$stage/source/Copying.txt" "$stage/stockfish/"
pnpm --dir apps/scan64-web install --frozen-lockfile
pnpm --dir apps/scan64-web build
uv run --no-sync pyinstaller --noconfirm --clean Scan64.spec
version=$(uv run --no-sync python -c 'from importlib.metadata import version; print(version("scan64"))')
archive="$PWD/dist/Scan64-${version}-macos-arm64.zip"
ditto -c -k --sequesterRsrc --keepParent "$PWD/dist/Scan64.app" "$archive"
printf '\nApp archive: %s\n' "$archive"
shasum -a 256 "$archive"
du -sh "$PWD/dist/Scan64.app" "$archive"
