#!/usr/bin/env bash
# Run the repository quality gate for the Python backend, scan64-web frontend
# and desktop shell. With no arguments it runs every section; otherwise it runs
# the named sections in the order given:
#
#   scripts/check.sh [python-tests] [python-static] [web] [desktop]
#
# CI runs each section as its own job, selected by scripts/ci_areas.py.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

web_built=0

python_tests() {
  echo "== python: pytest (4 workers) =="
  uv run pytest -m "not real_model" -n 4
}

python_static() {
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
}

web_build() {
  echo "== frontend: install =="
  (cd apps/scan64-web && pnpm install --frozen-lockfile)

  echo "== frontend: build =="
  (cd apps/scan64-web && pnpm build)
  web_built=1
}

web() {
  web_build

  echo "== frontend: lint =="
  (cd apps/scan64-web && pnpm lint)

  echo "== frontend: test =="
  (cd apps/scan64-web && pnpm test -- --run)

  echo "== frontend: e2e =="
  (cd apps/scan64-web && pnpm test:e2e)
}

desktop() {
  # The shell embeds the web client's production build (frontendDist).
  if [ "$web_built" -eq 0 ]; then
    web_build
  fi

  echo "== desktop: install =="
  (cd apps/scan64-desktop && pnpm install --frozen-lockfile)

  echo "== desktop: cargo fmt =="
  cargo fmt --manifest-path apps/scan64-desktop/src-tauri/Cargo.toml --check

  echo "== desktop: cargo clippy =="
  cargo clippy --locked --manifest-path apps/scan64-desktop/src-tauri/Cargo.toml --all-targets -- -D warnings

  echo "== desktop: cargo test =="
  cargo test --locked --manifest-path apps/scan64-desktop/src-tauri/Cargo.toml
}

if [ "$#" -eq 0 ]; then
  set -- python-tests python-static web desktop
fi

for section in "$@"; do
  case "$section" in
    python-tests) python_tests ;;
    python-static) python_static ;;
    web) web ;;
    desktop) desktop ;;
    *)
      echo "check.sh: unknown section '$section' (expected python-tests, python-static, web or desktop)" >&2
      exit 2
      ;;
  esac
done

echo "All checks passed: $*"
