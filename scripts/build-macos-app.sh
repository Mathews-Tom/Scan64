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
mkdir -p "$stage/tcl-tk"
for project in tcl tk; do
  curl --fail --location --retry 3 \
    "https://raw.githubusercontent.com/tcltk/$project/core-9-0-4/license.terms" \
    --output "$stage/tcl-tk/$project-license.terms"
done
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
