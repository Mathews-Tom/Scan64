#!/usr/bin/env bash
# Local verification: run every lint, type-check, and test gate for the
# Python backend and the scan64-web frontend. This script is the single
# source of truth for "green" and is invoked by the hosted CI workflow.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "== python: pytest =="
uv run pytest

echo "== python: ruff =="
uv run ruff check .

echo "== python: mypy --strict =="
uv run mypy --strict src/

echo "== python: dependency license check =="
uv run python scripts/check_licenses.py

echo "== frontend: install =="
(cd apps/scan64-web && pnpm install --frozen-lockfile)

echo "== frontend: build =="
(cd apps/scan64-web && pnpm build)

echo "== frontend: test =="
(cd apps/scan64-web && pnpm test -- --run)

echo "All checks passed."
