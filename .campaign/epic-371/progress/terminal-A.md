---
terminal: A
title: "Board+Pieces"
campaign: epic-371
wave: 1
status: paused
branch: feat/358-359-fide-board-pieces
writes_to: "boardAppearance.ts, pieces.css, Board.css, SettingsScreen*, board/piece assets, THIRD_PARTY_NOTICES.md, docs/user-guide.md board section"
issue_refs: "#359"
target: "All C.02 schemes; 2+ new Staunton sets; FIDE defaults"
blocked_by: []
started: 2026-11-25T22:32:24Z
updated: 2026-11-25T23:18:55Z
---

# Terminal A — Board+Pieces

## Issues

| Issue | Task | Target | Status | Actual |
|-------|------|--------|--------|--------|
| #359 | Add Staunton piece-set variations used in international competition and make one the default | All C.02 schemes; 2+ new Staunton sets; FIDE defaults | in_progress | 10 piece sets (8 Staunton-pattern, 2 new: mpchess GPL-3.0+, Sophia W3C); default cburnett; Settings grouped; 830 web tests pass; PR #375 |

## Results

## Log
- 2026-11-25T22:32:24Z — started
- 2026-11-25T22:40:19Z — commits ef44069 (sets, grouping, notices, tests) and b1ff77f (user guide) pushed; PR https://github.com/Mathews-Tom/Scan64/pull/375 opened. vitest 830 passed, lint 0, build ok.
- 2026-11-25T23:18:55Z — paused by operator during final check.sh; all commits pushed. Resume: /campaign resume A epic-371.
