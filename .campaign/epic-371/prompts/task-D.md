Working on documenting how the Scan64 narrator LLM consumes engine variations.
Repo: /home/syd/projects/Scan64 (you are in a worktree of it)
Branch: docs/361-narrator-pipeline (already checked out in your worktree; do not switch branches)

Issue: #361 — Document how the narrator LLM consumes engine variations
Read the issue body first: `gh issue view 361 -R Mathews-Tom/Scan64`.

Task:
- Write docs/narrator-pipeline.md. Change no code and no other file.
- Trace the data flow with file and function references: engine analysis (providers/stockfish) → analysis jobs and focused pass (chess/analysis) → diagnosis and evidence composition (learning/evidence) → explanation assembly and prompt (explanations/assembly.py) → provider (providers/llm) → validator (explanations/validator.py) → template fallback → API → client display. Cover all three callers of resolve_explanation (cli/analyse.py, chess/analysis/jobs.py, chess/analysis/coach_interruption.py) and the owner-grant flow (explanations/settings.py, api/explanation_settings.py).
- Include a mermaid flow diagram.
- List exactly which engine fields reach the LLM, and where variations are cut: adapter fields kept vs dropped, MultiPV count, focused_line length, single-move fields, payload serialisation format, local Ollama time and size limits.
- Describe the validator checks and every path that falls back to the template, and state that fallbacks are not logged today.
- End with a "Gaps" section that points to issues #360, #366, #367, #368, #369, and lists the steps to give the narrator full, long variations.

Target: the document is accurate against the code at your base commit (quote the commit hash in the document); every claim cites a file and symbol.
