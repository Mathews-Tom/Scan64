Working on narrating engine variations on the Scan64 Analysis Board for any position.
Repo: D:/projects/Scan64 (you are in a worktree of it)
Branch: feat/368-analysis-board-narration (already checked out in your worktree; do not switch branches)

Issue: #368 — Narrate engine variations on the Analysis Board for any position
Read the issue body first: `gh issue view 368 -R Mathews-Tom/Scan64`.

Context: waves 1 and 2 merged into your base:
- #360 configurable engine with full per-line fields (docs/engines.md).
- #366 claim-text validator; #367 structured per-move facts and JSON prompt (docs/narrator-pipeline.md).
- #369 narrator model benchmark (docs/evaluation/narrator-models.md).
Read docs/narrator-pipeline.md first.

Task:
- Find how the Analysis Board gets engine lines today (apps/scan64-web/src/components/AnalysisScreen.tsx and its API calls in src/scan64/api). Reuse that engine path and its admission rules; do not add a new engine pool.
- Add an API endpoint that takes a FEN (and optional MultiPV count and depth or node budget within existing limits) and returns an explanation of the top engine lines: for each line, the per-move facts from #367 and what the line wins, loses, or threatens from the engine evaluations along it; plus a comparison of the lines.
- Build a deterministic template explanation for this endpoint. The LLM path, when configured and granted, goes through the same grounding validator; any rejection falls back to the template.
- Support full-length principal variations from the engine.
- Web: add an "Explain lines" control on the Analysis Board that shows the explanation next to the lines, with each claim linked to its line (hover or click highlights the moves). Do not touch SettingsScreen or boardAppearance.ts.
- Tests: API tests (template path, LLM grounded path with a fake provider, rejection fallback, limits), web unit tests, one Playwright test of the control.
- Update docs/user-guide.md (Analysis Board) and docs/narrator-pipeline.md.

Target: any Analysis Board position yields a grounded explanation of at least 3 engine lines; all tests and scripts/check.sh pass.
