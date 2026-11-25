Working on a benchmark of chess-aware open LLMs as the Scan64 narrator against a general-purpose baseline.
Repo: /home/syd/projects/Scan64 (you are in a worktree of it)
Branch: feat/369-narrator-benchmark (already checked out in your worktree; do not switch branches)

Issue: #369 — Evaluate chess-aware open LLMs as the narrator against a general-purpose baseline
Read the issue body first: `gh issue view 369 -R Mathews-Tom/Scan64`.

Context: wave 1 merged into your base: the claim-text validator (#366) and docs/narrator-pipeline.md (#361). Another terminal changes the prompt builder in parallel (#367); benchmark the prompt builder in your base, record the commit hash, and make the prompt builder a pluggable input so a rerun after #367 needs no code change.

Task:
- Survey candidates. Verify each exists on Hugging Face and record licence, size, base model, training data, and GGUF availability: ChessGPT (Waterhorse/chessgpt-base-v1, chessgpt-chat-v1), concept-guided chess commentary (CCC) models, chess instruction fine-tunes you find, and strong general models (for example Qwen3 8B/14B, Llama 3.1 8B, Gemma 3 12B). Pick at most 6 models including one general baseline of similar size to the best chess candidate.
- Serve models locally: Ollama (user-level install from https://github.com/ollama/ollama/releases into ~/.local/opt/ollama if missing) or llama.cpp server / vLLM via the app's OpenAI-compatible hosted adapter. Use only the GPU on this machine (RTX 5090, 32 GB). Stop all servers when done.
- Build benchmarks/narrator/: a TOML config (models, endpoints, positions, runs), a saved runner script, and a fixed set of 50 positions with evidence built from existing test fixtures or recorded diagnosis references in the repo. Run each model 3 times per position.
- Metrics per model: grounding-check pass rate (the #366 validator), false chess statements per explanation (validator rejection reasons), SAN correctness, latency p50/p95, VRAM use.
- Keep it small: this answers a practical question, not a paper. Do not expand beyond 6 models x 50 positions x 3 runs.
- Write results to benchmarks/narrator/results/ (JSON) and a report docs/evaluation/narrator-models.md with a table and one recommendation, or the statement that no candidate beats the baseline.
- Post the result table as a comment on #369.
- Do not add any model to the app's defaults; selection stays by configuration.

Target: benchmark reproducible with one command; report and #369 comment posted.
