Working on grounding the narrator's claim text in engine and board evidence.
Repo: /home/syd/projects/Scan64 (you are in a worktree of it)
Branch: feat/366-ground-claim-text (already checked out in your worktree; do not switch branches)

Issue: #366 — Ground the narrator's claim text, not only its cited lines, in engine evidence
Read the issue body first: `gh issue view 366 -R Mathews-Tom/Scan64`.

Task:
- Today validate_generated_explanation (src/scan64/explanations/validator.py) checks evidence_ref, certainty, disclosure_level and the cited `line`, but accepts any `text`. Close that gap.
- Extract chess facts from claim text: SAN and UCI moves, squares, piece names with colour, evaluation numbers (pawns, centipawns, mate-in-N), material words (wins a pawn, loses the queen, exchange), side to move, check/mate words.
- Check each extracted fact against the referenced evidence payload (see src/scan64/learning/evidence/composer.py for payload keys) and the board from the FEN with python-chess. A move in text must be legal at the right point and appear in a verified line or payload field. A piece on a square must be on that square. An evaluation must match evidence within a stated tolerance. Unsupported facts reject the generation.
- Keep it deterministic. Prefer a small explicit grammar and vocabulary over heuristics; reject text you cannot parse when it contains chess tokens.
- Log each rejection with a reason code (structured log, no evidence payload contents) and keep an in-process counter of template fallbacks by reason. Expose the counter where existing diagnostics or settings status endpoints fit, if one exists; otherwise logging only.
- Do not change how the prompt is built; another terminal does that later.
- Tests: an adversarial fixture set (invented moves, wrong piece, wrong square, invented evaluation, wrong side to move, illegal move in text, move not in any verified line) that is rejected; a correct fixture set that passes. At least 15 adversarial and 10 correct cases.

Target: all fixtures behave as stated; existing explanation tests pass; rejection reasons are logged and counted.
