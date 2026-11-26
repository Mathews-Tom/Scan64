# Narrator benchmark: finish on the RTX 5090 (Linux)

Ran on the RTX 4080 Laptop (12 GB), 150/150 calls each: chessgpt-base, praxis-4b,
qwen3-4b-chess-explain, chesslm-qwen3-8b. Remaining: `qwen3.5-4b` (60/150 done) and
`qwen3-8b` (0/150).

`--resume` refuses to run if `src/scan64/explanations/assembly.py` differs from the
hash in the runs file header (`255ce24b73f9…`). Run from the tip of
`feat/369-narrator-benchmark`, not from `epic/371`: later epic work (#367) changes
that module.

```bash
git fetch origin feat/369-narrator-benchmark
git worktree add --detach ../scan64-bench origin/feat/369-narrator-benchmark
cd ../scan64-bench
uv sync --locked

# Ollama >= 0.40 (0.17 cannot pull qwen3.5) on the benchmark port.
OLLAMA_HOST=127.0.0.1:11435 ollama serve &

uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml \
  --setup --models qwen3.5-4b qwen3-8b \
  --resume benchmarks/narrator/results/runs_20261126_145813.jsonl
```

`--setup` creates all six Ollama models (it downloads the four GGUF files too, about
12 GB). The run measures only the two selected models and skips lines already in the
runs file. It writes `summary_20261126_145813.json` and `.md` next to the runs file.

Return these three files:

- `benchmarks/narrator/results/runs_20261126_145813.jsonl`
- `benchmarks/narrator/results/summary_20261126_145813.json`
- `benchmarks/narrator/results/summary_20261126_145813.md`

Caveat: the runs file header records the RTX 4080 Laptop. Latency and the 4 s
production-limit share for `qwen3.5-4b` and `qwen3-8b` come from the RTX 5090 and are
not comparable with the other four. Acceptance, rejected-claim and fallback-reason
counts do not depend on the GPU.

Known defect found by this run: the #366 claim-text validator raises StopIteration
for claim text naming a "back-rank" tactic (its regex accepts a hyphen, the
`_TACTIC_WORDS` keys use a space). The runner records those calls as
`pipeline_exception`. The validator fix belongs in `epic/371`.
