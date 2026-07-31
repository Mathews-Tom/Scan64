#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v stockfish >/dev/null; then
  echo "Stockfish must be available as 'stockfish' on PATH." >&2
  exit 1
fi

api_pid=""
web_pid=""

cleanup() {
  if [[ -n "$web_pid" ]]; then
    pkill -TERM -P "$web_pid" 2>/dev/null || true
  fi

  for pid in "$api_pid" "$web_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid"
    fi
  done

  for pid in "$api_pid" "$web_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

uv run uvicorn scan64.api.app:app --host 127.0.0.1 --port 8001 &
api_pid=$!
for _ in {1..50}; do
  if curl --fail --silent http://127.0.0.1:8001/health >/dev/null; then
    break
  fi
  if ! kill -0 "$api_pid" 2>/dev/null; then
    wait "$api_pid" || true
    echo "Scan64 API failed to start." >&2
    exit 1
  fi
  sleep 0.1
done
if ! curl --fail --silent http://127.0.0.1:8001/health >/dev/null; then
  echo "Scan64 API did not become ready." >&2
  exit 1
fi

pnpm --dir apps/scan64-web dev --host 127.0.0.1 &
web_pid=$!
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do
  sleep 0.1
done
echo "A Scan64 server stopped unexpectedly." >&2
exit 1
