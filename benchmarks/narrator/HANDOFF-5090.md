# Narrator benchmark: finish on the RTX 5090

Four of six models ran on the RTX 4080 Laptop (12 GB): chessgpt-base, praxis-4b,
qwen3-4b-chess-explain, chesslm-qwen3-8b. Two remain: `qwen3.5-4b` and `qwen3-8b`.

`--resume` refuses to run unless `git rev-parse HEAD` equals the commit in the runs
file header (`8d8cebb80bbacdb4d949a3ca4fc738ac9f0c70a7`) and `src/` is clean. Run it
from a detached checkout of that commit.

```bash
git fetch origin feat/369-narrator-benchmark
git worktree add --detach ../scan64-bench 8d8cebb80bbacdb4d949a3ca4fc738ac9f0c70a7
cd ../scan64-bench
git restore --source origin/feat/369-narrator-benchmark -- \
  benchmarks/narrator/results/runs_20261126_145813.jsonl
uv sync --locked

# Ollama >= 0.40 (0.17 cannot pull qwen3.5) on the benchmark port.
OLLAMA_HOST=127.0.0.1:11435 ollama serve &

uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml \
  --setup --models qwen3.5-4b qwen3-8b \
  --resume benchmarks/narrator/results/runs_20261126_145813.jsonl
```

`--setup` creates all six Ollama models (it downloads the four GGUF files too, about
12 GB). The run measures only the two selected models and skips existing lines. It
writes `summary_20261126_145813.json` and `.md` next to the runs file.

Return these three files:

- `benchmarks/narrator/results/runs_20261126_145813.jsonl`
- `benchmarks/narrator/results/summary_20261126_145813.json`
- `benchmarks/narrator/results/summary_20261126_145813.md`

Caveat: the runs file header records the RTX 4080 Laptop. Latency and the 4 s
production-limit share for `qwen3.5-4b` and `qwen3-8b` come from the RTX 5090 and are
not comparable with the other four. Acceptance, rejected-claim and fallback-reason
counts do not depend on the GPU.
