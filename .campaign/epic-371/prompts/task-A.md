Working on Staunton piece-set variations for the Scan64 web client.
Repo: D:/projects/Scan64 (you are in a worktree of it)
Branch: feat/358-359-fide-board-pieces (already checked out in your worktree; do not switch branches)

Issue: #359 — Add Staunton piece-set variations used in international competition and make one the default
Read the issue body first: `gh issue view 359 -R Mathews-Tom/Scan64`.

PR #373 (FIDE board palettes, C.02 citation, restored Slate/Sage/Graphite) is already merged into epic/371; #358 is done. Do not change board palettes. Read `gh pr view 373 -R Mathews-Tom/Scan64` for its FIDE C.02 reading.

Task:
- Read FIDE Handbook C.02 "Standards of Chess Equipment" (https://handbook.fide.com/chapter/C02Standards) for the piece rules: Staunton pattern, proportions, colours, non-shiny finish.
- Add Staunton-pattern piece sets that tournaments and broadcasts use widely, only if their licence allows GPL-3.0 distribution (CC0, CC BY, CC BY-SA, GPL, MIT, Apache, OFL). Good sources: the lichess-org/lila piece sets (check each set's licence in that repo's COPYING/README). Keep a Staunton set as the default.
- Mark the Staunton-pattern sets in the Settings UI (for example a group heading or label). Do not remove or rename existing options. A saved choice must keep working.
- Record every new asset in THIRD_PARTY_NOTICES.md with source URL and licence.
- Update docs/user-guide.md (board appearance part) with a short table: piece set, Staunton pattern yes/no, licence.
- Tests: update SettingsScreen tests for new options, default, and saved-choice persistence. Keep within Vitest's timeout (see PR #373's note on stepping through options).

Target: at least 2 new licence-compatible Staunton sets (or a documented reason in the PR body why fewer exist); the default is Staunton; web tests and lint pass.
