#!/usr/bin/env bash
# Run every local lint, type-check, and test gate for the Python backend,
# scan64-web frontend, and desktop shell. This is the local quality gate.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "== python: pytest =="
uv run pytest -m "not real_model"

echo "== python: ruff =="
uv run ruff check src tests scripts benchmarks

echo "== python: ruff format =="
uv run ruff format --check .

echo "== python: mypy --strict =="
uv run mypy --strict src/

echo "== python: recorded real-game diagnosis precision =="
uv run python -m scan64.learning.evaluation.real_games --check

echo "== python: dependency license check =="
uv run python scripts/check_licenses.py

echo "== frontend: install =="
(cd apps/scan64-web && pnpm install --frozen-lockfile)

echo "== frontend: build =="
(cd apps/scan64-web && pnpm build)

echo "== frontend: lint =="
(cd apps/scan64-web && pnpm lint)

echo "== frontend: test =="
(cd apps/scan64-web && pnpm test -- --run)

echo "== frontend: e2e =="
(cd apps/scan64-web && pnpm test:e2e)

echo "== desktop: install =="
(cd apps/scan64-desktop && pnpm install --frozen-lockfile)

echo "== desktop: cargo fmt =="
cargo fmt --manifest-path apps/scan64-desktop/src-tauri/Cargo.toml --check

echo "== desktop: cargo clippy =="
cargo clippy --locked --manifest-path apps/scan64-desktop/src-tauri/Cargo.toml --all-targets -- -D warnings

echo "== desktop: cargo test =="
cargo test --locked --manifest-path apps/scan64-desktop/src-tauri/Cargo.toml

echo "All checks passed."
