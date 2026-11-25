#!/usr/bin/env bash
# Install the Scan64 toolchain under ~/.local (no sudo). Idempotent.
# Stockfish 18: same source tag and checksums as the repo Dockerfile.
set -euo pipefail

ROOT="/home/syd/projects/Scan64"
OPT="${HOME}/.local/opt"
BIN="${HOME}/.local/bin"
mkdir -p "${OPT}" "${BIN}"

# ── Stockfish 18 ───────────────────────────────────────────
if ! "${BIN}/stockfish" <<<"quit" 2>/dev/null | grep -q "Stockfish 18"; then
  echo "== Building Stockfish 18 =="
  work="$(mktemp -d)"
  curl --fail --location --retry 3 \
    https://codeload.github.com/official-stockfish/Stockfish/tar.gz/refs/tags/sf_18 \
    --output "${work}/sf_18.tar.gz"
  echo "22a195567e3493e7c9ca8bf8fa2339f4ffc876384849ac8a417ff4b919607e7b  ${work}/sf_18.tar.gz" | sha256sum --check
  mkdir "${work}/src" && tar -xzf "${work}/sf_18.tar.gz" -C "${work}/src" --strip-components=1
  for nn in c288c895ea92 37f18f62d772; do
    curl --fail --location --retry 3 "https://tests.stockfishchess.org/api/nn/nn-${nn}.nnue" \
      --output "${work}/src/src/nn-${nn}.nnue"
  done
  echo "c288c895ea924429ea9092e3f36b2b3c1f00f2a3a4c759ff7e57e79e3b43e4a7  ${work}/src/src/nn-c288c895ea92.nnue" | sha256sum --check
  echo "37f18f62d772f3107e1d6aaca3898c130c3c86f2ab63e6555fbbca20635a899d  ${work}/src/src/nn-37f18f62d772.nnue" | sha256sum --check
  make -C "${work}/src/src" -j16 build ARCH=x86-64-avx2 COMP=gcc
  install -m 0755 "${work}/src/src/stockfish" "${BIN}/stockfish"
  rm -rf "${work}"
fi
"${BIN}/stockfish" <<<"quit" | head -1

# ── Node 24 (checksum-verified) ────────────────────────────
if ! "${OPT}/node24/bin/node" --version 2>/dev/null | grep -q '^v24\.'; then
  echo "== Installing Node 24 =="
  ver="$(curl -fsSL https://nodejs.org/dist/index.json | python3 -I -c 'import json,sys; print(next(r["version"] for r in json.load(sys.stdin) if r["version"].startswith("v24.")))')"
  work="$(mktemp -d)"
  tarball="node-${ver}-linux-x64.tar.xz"
  curl -fsSL "https://nodejs.org/dist/${ver}/${tarball}" -o "${work}/${tarball}"
  curl -fsSL "https://nodejs.org/dist/${ver}/SHASUMS256.txt" -o "${work}/SHASUMS256.txt"
  (cd "${work}" && grep " ${tarball}\$" SHASUMS256.txt | sha256sum --check)
  tar -xJf "${work}/${tarball}" -C "${OPT}"
  ln -sfn "${OPT}/node-${ver}-linux-x64" "${OPT}/node24"
  rm -rf "${work}"
fi
export PATH="${OPT}/node24/bin:${BIN}:${PATH}"
node --version

# ── pnpm 10.33.3 (pinned by apps/scan64-web/package.json) ──
if ! pnpm --version 2>/dev/null | grep -qx '10.33.3'; then
  echo "== Installing pnpm 10.33.3 =="
  npm install --global pnpm@10.33.3
fi
pnpm --version

# ── Main checkout dependencies + Playwright browser ────────
cd "${ROOT}"
uv sync --locked
pnpm install --frozen-lockfile
pnpm --dir apps/scan64-web exec playwright install chromium

echo "Toolchain ready."
