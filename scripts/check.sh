#!/usr/bin/env bash
# Run every local lint, type-check, and test gate for the Python backend and
# the scan64-web frontend. This script is the source of truth for verified
# local quality.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "== python: pytest =="
uv run pytest -m "not real_model"

echo "== python: ruff =="
uv run ruff check src tests scripts benchmarks

echo "== python: mypy --strict =="
uv run mypy --strict src/

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

echo "All checks passed."
