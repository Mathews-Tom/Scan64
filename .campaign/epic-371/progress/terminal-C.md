---
terminal: C
title: "Grounding"
campaign: epic-371
wave: 1
status: paused
branch: feat/366-ground-claim-text
writes_to: "src/scan64/explanations/validator.py, src/scan64/explanations/assembly.py (fallback logging only), explanation tests, chess-lesson-spec claims (only if required)"
issue_refs: "#366"
target: "15+ adversarial rejected, 10+ correct pass; fallbacks logged"
blocked_by: []
started: 2026-11-25T22:35:49Z
updated: 2026-11-25T23:18:55Z
---

# Terminal C — Grounding

## Issues

| Issue | Task | Target | Status | Actual |
|-------|------|--------|--------|--------|
| #366 | Ground the narrator's claim text, not only its cited lines, in engine evidence | 15+ adversarial rejected, 10+ correct pass; fallbacks logged | in_progress | 24 adversarial rejected, 14 correct pass (tests/unit/test_claim_text_grounding.py, 47 tests); fallbacks logged+counted by reason (assembly.template_fallback_counts) |

## Results

## Log
- 2026-11-25T22:35:49Z: started; read issue #366, validator, assembly, composer, existing tests.
- 2026-11-25T22:44:09Z: commits 72ef701 (validator grammar + fallback logging/counter + tests), 11b4c51 (docs); pushed; PR https://github.com/Mathews-Tom/Scan64/pull/376. Related tests 239 passed; ruff+mypy clean. Running scripts/check.sh next.
- 2026-11-25T23:18:55Z — paused by operator during final check.sh; all commits pushed. Resume: /campaign resume C epic-371.
