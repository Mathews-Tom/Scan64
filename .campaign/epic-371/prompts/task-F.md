Working on giving the Scan64 narrator precomputed chess facts per move instead of raw UCI payloads.
Repo: /home/syd/projects/Scan64 (you are in a worktree of it)
Branch: feat/367-narrator-facts (already checked out in your worktree; do not switch branches)

Issue: #367 — Give the narrator precomputed chess facts per move instead of raw UCI payloads
Read the issue body first: `gh issue view 367 -R Mathews-Tom/Scan64`.

Context: wave 1 merged into your base:
- #360: configurable engine with extended per-line fields (full pv, wdl, depth, nodes). See docs/engines.md.
- #366: the validator now checks chess facts in claim text. See src/scan64/explanations/validator.py.
- #361: docs/narrator-pipeline.md describes the current pipeline. Read it first.

Task:
- Replace the Python-repr evidence dump in _explanation_request (explanations/assembly.py) with structured JSON that follows a stated JSON Schema (store the schema in the repo next to the other schemas in schemas/).
- For each move in each verified line, compute with python-chess: ply, side, SAN, UCI, piece moved, capture (piece taken), check/mate, promotion, castling, material balance after the move, new threats (pieces attacked and not defended after the move). Add the engine evaluation after the move from persisted analyses when present; otherwise mark it unknown. Do not run new engine searches in the explanation path.
- Add position facts: side to move, evaluation before and after the played move, the best move and its evaluation, the evaluation difference, and the mistake class from the diagnosis.
- Make the validator from #366 accept claims that use these facts and reject claims beyond them. Add tests.
- Local Ollama limits (providers/llm/local_ollama.py): make the operation timeout and byte limits configurable with documented defaults that let a supported local 7-14B model finish (measure on this machine's GPU; Ollama may need a user-level install from https://github.com/ollama/ollama/releases into ~/.local/opt/ollama, run `ollama serve` only during your test and stop it after).
- Update the system prompt to tell the model to use only the given facts.
- Update docs/narrator-pipeline.md and the narrator part of docs/system-design.md.

Target: prompt is schema-valid JSON for every diagnosis kind in the test fixtures; validator tests pass; one measured local-model run with timings recorded in the PR body.
