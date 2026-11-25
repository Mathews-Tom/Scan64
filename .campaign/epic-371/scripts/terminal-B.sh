#!/usr/bin/env bash
# Terminal B — Engine (wave 1) — #360
exec bash "/home/syd/projects/Scan64/.campaign/epic-371/scripts/run-terminal.sh" "B" "feat/360-swappable-engine" "#360" "src/scan64/providers/stockfish/, src/scan64/providers/engine/ (new), src/scan64/chess/analysis/models.py, engine config, engine tests, benchmarks/engines/ (new), docs/system-design.md, docs/engines.md (new)" "${1:-fresh}"
