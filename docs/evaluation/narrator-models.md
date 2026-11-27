# Narrator model evaluation (#369, #389)

> **2026-11-26 rerun on the #367 prompt: the 2026-11-26 `praxis-4b` recommendation is out of date.** The rerun ([`summary_20261126_230005`](../../benchmarks/narrator/results/summary_20261126_230005.md), 9 models, RTX 5090) gives `praxis-4b` a 4.0% grounding pass; 56% of its calls hit the output cap, and its 0.00 false chess statements come from answers that never parse. The #389 decision below replaces that recommendation; the pre-#367 runs are under [Earlier runs](#earlier-runs-pre-367-pipeline-only).

Question: which open LLM words Scan64 engine evidence best as the narrator? The engine judges. The model only words engine and board facts. The validator decides whether its claims replace the template explanation.

Decision: use `gemma4-12b` (Ollama `gemma4:12b`, Apache-2.0). On GPUs with less than 10 GiB free, use `praxis-4b`. See [Decision](#decision). This replaces the 2026-11-26 choice of `praxis-4b`, which was measured on the pre-#367 prompt only.

All numbers below are measured. Raw records and summaries are in `benchmarks/narrator/results/`.

## Shortlist (9 models)

| Model (benchmark name) | Role | Licence | Params (B) | Source | Base model | GPU memory (GiB) |
|---|---|---|---|---|---|---|
| `praxis-2b` | chess | Apache-2.0 | 2.27 | GGUF `praxis-chess/praxis-chess-reasoner-qwen3.5-2b-GGUF` | Qwen3.5-2B + LoRA (engine evidence graphs) | 2.05 |
| `second-2b` | chess | Apache-2.0 | 1.88 | GGUF `mradermacher/Second-1.0-2B-GGUF` | Qwen3.5-2B, SFT + DAPO on rule-checked Stockfish-fact explanations | 2.06 |
| `qwen3.5-2b` | baseline | Apache-2.0 | 2.27 | Ollama `qwen3.5:2b` | Qwen3.5-2B | 3.58 |
| `praxis-4b` | chess | Apache-2.0 | 4.2 | GGUF `praxis-chess/praxis-chess-reasoner-qwen3.5-4b-GGUF` | Qwen3.5-4B + LoRA (engine evidence graphs) | 3.67 |
| `qwen3.5-4b` | baseline | Apache-2.0 | 4.7 | Ollama `qwen3.5:4b` | Qwen3.5-4B | 4.49 |
| `gemma4-e4b` | baseline | Apache-2.0 | 8.0 (4.5 effective) | Ollama `gemma4:e4b` | google/gemma-4-E4B-it | 5.28 |
| `qwen3.5-9b` | baseline | Apache-2.0 | 9.65 | Ollama `qwen3.5:9b` | Qwen3.5-9B | 7.01 |
| `granite4.2-8b` | baseline | Apache-2.0 | 8.79 | Ollama `granite4.2:8b` | ibm-granite/granite-4.2-8b | 7.48 |
| `gemma4-12b` | baseline | Apache-2.0 | 11.95 | Ollama `gemma4:12b` | google/gemma-4-12b-it | 9.43 |

Every GGUF is pinned by repository revision and sha256 in `benchmarks/narrator/narrator.toml`; library models are pinned by the `ollama_digest` in each runs file. GPU memory is the measured rise in `nvidia-smi` memory when the model loads with `num_ctx` 12288 (weights, KV cache and buffers). Ollama's `size_vram` under-reports the gemma4 models.

Dropped from the first shortlist: `chessgpt-base`, `qwen3-4b-chess-explain`, `chesslm-qwen3-8b`, `qwen3-8b` (see [Earlier runs](#earlier-runs-pre-367-pipeline-only)). `Caissa-Chess-M1` was dropped because it is a chain-of-thought reasoning model; the narrator must not reason.

## Method

- Command, repository root, Ollama on 127.0.0.1:11435: `uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml`.
- Positions: 50 fixed positions in `benchmarks/narrator/positions.json`, built by `build_positions.py` from the m48 real-game corpora. Each model runs 3 times per position (150 calls per model).
- Pipeline: the production `scan64.explanations.assembly:resolve_explanation` with an `OllamaExplanationAdapter` from `create_llm_provider`. The runs file header pins the commit, the `assembly.py` sha256, the runner sha256 and `dirty`.
- Request: `think = false` and `stream = true` in every `/api/chat` body. `num_ctx` 12288 and `num_predict` 2048 in every Modelfile. Client timeout 120 s.
- Hardware: NVIDIA GeForce RTX 5090 32 GB, Ollama 0.40.18. No other Ollama server held a model during any call.

### Metrics

| Metric | Definition |
|---|---|
| Grounding pass | Share of calls where the pipeline accepts the model output instead of the template. |
| Coverage (accepted) | Share of three key engine facts (played move, key move, result) that an accepted answer states. On the slot path it counts only the slots the answer uses (`slot_coverage`). |
| Delivered coverage | Coverage over all calls; a fallback counts as 0. This is what a learner gets. |
| Full coverage | Share of all calls accepted with all three key facts. |
| False chess statements / expl. | Claims rejected for a chess reason, per explanation. On the slot path this includes `chess_token_outside_slot`. |
| Latency p50 / p95 | Wall time of the provider call. |
| First token p50 | Time to the first streamed token. |
| Hit output cap | Share of calls that stop at `num_predict` (`done_reason = length`). |
| Calls that thought | Calls with thinking text or a `<think>` tag in the answer. Must be 0. |
| GPU memory | Measured load rise in `nvidia-smi`; null without `nvidia-smi`. |

## Results

### Slot pipeline (#386), coach voice

`summary_20261127_161413.{md,json}`, runs in `runs_20261127_161413.jsonl`. Commit `aba0e5d`, `assembly.py` sha256 `9fc28043b51e`, runner sha256 `8a0b6d8794b0`, `dirty: false`, voice `coach`.

| Model | Grounding pass | Coverage (accepted) | Delivered coverage | Full coverage | False chess statements / expl. | Latency p50 / p95 (s) | First token p50 (s) | Hit output cap | Calls that thought | GPU memory (GiB) |
|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-12b | 46.0% | 89.9% | **41.3%** | 34.7% | 0.57 | 2.03 / 2.52 | 1.04 | 0.0% | 0 | 9.43 |
| praxis-4b | 44.0% | 83.3% | 36.7% | 24.0% | 0.92 | 0.82 / 1.72 | 0.40 | 0.0% | 0 | 3.67 |
| gemma4-e4b | 45.3% | 74.5% | 33.8% | 21.3% | 0.68 | 0.75 / 1.12 | 0.29 | 0.0% | 0 | 5.28 |
| qwen3.5-4b | 34.7% | 67.9% | 23.6% | 14.0% | 1.05 | 1.02 / 2.24 | 0.40 | 0.0% | 0 | 4.49 |
| granite4.2-8b | 25.3% | 57.9% | 14.7% | 7.3% | 1.42 | 1.99 / 2.99 | 0.78 | 0.0% | 0 | 7.48 |
| qwen3.5-9b | 12.7% | 77.2% | 9.8% | 6.7% | 1.69 | 1.82 / 4.37 | 0.58 | 0.0% | 0 | 7.01 |
| qwen3.5-2b | 2.0% | 77.8% | 1.6% | 1.3% | 2.94 | 1.18 / 4.51 | 0.09 | 4.0% | 0 | 3.58 |
| second-2b | 0.0% | — | 0.0% | 0.0% | 1.53 | 0.61 / 1.22 | 0.09 | 0.0% | 0 | 2.06 |
| praxis-2b | 0.0% | — | 0.0% | 0.0% | 3.12 | 0.98 / 4.63 | 0.20 | 20.0% | 0 | 2.05 |

Calls that thought: 0 for every model.

### Free-prose pipeline (#367) against slots

`summary_20261126_230005.{md,json}` (commit `940d7ed`, `assembly.py` sha256 `534ac8bc1381`, same positions, hardware and request settings, no voice line). Both runs: 150 calls per model.

| Model | Grounding pass, prose → slots | Delivered coverage, prose → slots | Full coverage, prose → slots | False chess statements / expl., prose → slots | Latency p50 (s), prose → slots | Hit output cap, prose → slots |
|---|---|---|---|---|---|---|
| gemma4-12b | 78.7% → 46.0% | 72.7% → 41.3% | 60.7% → 34.7% | 0.17 → 0.57 | 1.68 → 2.03 | 0.0% → 0.0% |
| praxis-4b | 4.0% → 44.0% | 2.7% → 36.7% | 0.0% → 24.0% | 0.00 → 0.92 | 7.35 → 0.82 | 56.0% → 0.0% |
| gemma4-e4b | 84.0% → 45.3% | 51.1% → 33.8% | 20.0% → 21.3% | 0.06 → 0.68 | 0.55 → 0.75 | 0.0% → 0.0% |
| qwen3.5-4b | 47.3% → 34.7% | 37.1% → 23.6% | 24.0% → 14.0% | 0.53 → 1.05 | 1.29 → 1.02 | 0.0% → 0.0% |
| granite4.2-8b | 60.0% → 25.3% | 55.1% → 14.7% | 46.7% → 7.3% | 0.46 → 1.42 | 1.52 → 1.99 | 0.0% → 0.0% |
| qwen3.5-9b | 44.0% → 12.7% | 34.9% → 9.8% | 23.3% → 6.7% | 0.90 → 1.69 | 1.64 → 1.82 | 0.0% → 0.0% |
| qwen3.5-2b | 12.7% → 2.0% | 8.2% → 1.6% | 4.0% → 1.3% | 1.91 → 2.94 | 1.32 → 1.18 | 0.0% → 4.0% |
| second-2b | 34.0% → 0.0% | 22.2% → 0.0% | 7.3% → 0.0% | 0.33 → 1.53 | 0.56 → 0.61 | 0.0% → 0.0% |
| praxis-2b | 0.0% → 0.0% | 0.0% → 0.0% | 0.0% → 0.0% | 0.50 → 3.12 | 4.47 → 0.98 | 80.0% → 20.0% |

The slot pipeline lowers delivered coverage for 8 of 9 models. Only `praxis-4b` gains: on free prose it hit the output cap in 56% of calls; with slots it writes short answers. The false-statement counts of the two pipelines measure different things: on the slot path a chess word in the prose is a false statement even when the fact is true.

### Why slot answers fall back

Fallback reasons, slot run (calls out of 150):

| Model | `chess_token_outside_slot` | `unknown_slot` | `malformed_line_move` | Other |
|---|---|---|---|---|
| gemma4-12b | 79 | 1 | 0 | 1 |
| praxis-4b | 12 | 63 | 9 | 0 |
| gemma4-e4b | 73 | 5 | 1 | 3 |
| qwen3.5-4b | 55 | 4 | 37 | 2 |
| granite4.2-8b | 105 | 0 | 0 | 7 |

The `CHESS_PROSE` rule rejects common chess verbs and nouns outside a slot. Over all 9 models, the words most often rejected are `check` (172 claims), `capture` (131), digits (one per digit, 62–96 each), `wins` (89), `mate` (81), `threat` (75) and `knight` (67). For `gemma4-12b` the top words are `wins` 27, `capture` 24, `mate` 17 and `check` 13, for example "After {move:L3:1}, {capture:L3:2} wins this piece in its own local exchange." `praxis-4b` loses 63 calls to one answer, "You played {played}, but {best} was stronger: {capture:best}.", which names a capture slot that a position without a capture does not have.

Prompt length: the slot list adds about 2,100 tokens. Mean prompt tokens rise from 4,070–4,238 to 6,174–6,383; the longest prompt is 9,869 tokens. 12–15 calls per model (4–5 of 50 positions) have prompts of 8,192 tokens or more.

### Voice comparison (#387)

Top 3 models by delivered coverage, run once more with each other voice. `coach` is the main run above; `terse` is `summary_20261127_171614` (`narrator_voice_terse.toml`); `playful` is `summary_20261127_181731` (`narrator_voice_playful.toml`). Same commit pipeline (`assembly.py` sha256 `9fc28043b51e`), 150 calls per row, every header `dirty: false`.

| Model | Voice | Grounding pass | Delivered coverage | Full coverage | False chess statements / expl. | Latency p50 / p95 (s) | Output tokens (mean) | Calls that thought |
|---|---|---|---|---|---|---|---|---|
| gemma4-12b | coach | 46.0% | 41.3% | 34.7% | 0.57 | 2.03 / 2.52 | 262 | 0 |
| gemma4-12b | terse | 42.0% | 36.9% | 29.3% | 0.58 | 1.89 / 2.42 | 225 | 0 |
| gemma4-12b | playful | 46.0% | 41.8% | 33.3% | 0.53 | 1.99 / 2.47 | 250 | 0 |
| praxis-4b | coach | 44.0% | 36.7% | 24.0% | 0.92 | 0.82 / 1.72 | 156 | 0 |
| praxis-4b | terse | 50.0% | 42.0% | 28.0% | 0.29 | 0.79 / 1.27 | 144 | 0 |
| praxis-4b | playful | 46.0% | 38.7% | 26.0% | 0.36 | 0.76 / 1.44 | 115 | 0 |
| gemma4-e4b | coach | 45.3% | 33.8% | 21.3% | 0.68 | 0.75 / 1.12 | 196 | 0 |
| gemma4-e4b | terse | 54.0% | 39.8% | 26.7% | 0.48 | 0.69 / 1.07 | 169 | 0 |
| gemma4-e4b | playful | 48.7% | 36.0% | 24.0% | 0.68 | 0.80 / 1.30 | 210 | 0 |

Mean delivered coverage over the three voices: `gemma4-12b` 40.0%, `praxis-4b` 39.1%, `gemma4-e4b` 36.5%. The voice changes delivered coverage by at most 6.0 points per model. `terse` helps the two smaller models and costs `gemma4-12b` 4.4 points. `gemma4-12b` has the highest full coverage in every voice.

Run-to-run noise: a first playful run, discarded because its header was `dirty` (an uncommitted report edit), gave `gemma4-12b` 43.8%, `praxis-4b` 38.7% and `gemma4-e4b` 35.3% delivered coverage. Differences up to 2.0 points between two runs of one config are noise at 150 calls.

## Decision

2026-11-27: the narrator model is `gemma4-12b`. The fallback for GPUs with less than 10 GiB free is `praxis-4b`. The ranking key is delivered coverage, with grounding pass as the second key.

| Key | gemma4-12b | praxis-4b | gemma4-e4b |
|---|---|---|---|
| Delivered coverage, coach (default voice) | **41.3%** | 36.7% | 33.8% |
| Delivered coverage, mean of 3 voices | **40.0%** | 39.1% | 36.5% |
| Full coverage, coach | **34.7%** | 24.0% | 21.3% |
| Grounding pass, coach | **46.0%** | 44.0% | 45.3% |
| False chess statements / expl., coach | **0.57** | 0.92 | 0.68 |
| Delivered coverage, free prose (#367) | **72.7%** | 2.7% | 51.1% |
| Latency p50 / p95, coach (s) | 2.03 / 2.52 | 0.82 / 1.72 | 0.75 / 1.12 |
| GPU memory (GiB) | 9.43 | **3.67** | 5.28 |

- `gemma4-12b` leads delivered coverage in the default voice and the voice mean. It also leads on the free-prose pipeline, so the choice does not depend on the slot rules.
- It needs 9.43 GiB with `num_ctx` 12288. A GPU with less than 10 GiB free cannot hold it with the desktop and other buffers.
- Fallback below 10 GiB: `praxis-4b`, 3.67 GiB. It is second in delivered coverage (coach and voice mean) and leads in `terse`. Its false chess statements are lower than `gemma4-e4b` in `terse` and `playful`, higher in `coach`.
- The gap between `praxis-4b` and `gemma4-e4b` is 2.6 points (voice mean), close to the run-to-run noise. The lower memory decides for `praxis-4b`.

The rule for a later change stays: rerun this benchmark when the prompt builder, the slot rules, the validator or a candidate changes. A model replaces `gemma4-12b` only when it has a higher delivered coverage and no more false chess statements in the same run.

## Follow-ups

- Slot rules: the slot pipeline lowers delivered coverage for 8 of 9 models, mostly through `chess_token_outside_slot` on verbs (`wins`, `capture`, `check`, `mate`). Slots or allowed words for these verbs can recover part of the free-prose coverage.
- `praxis-4b` copies the example slot `{capture:best}` from the prompt in 42–63 of 150 calls. An example that uses only slots every position has would remove these fallbacks.
- `think = false` in production: the app adapters send no `think` field. The benchmark numbers assume it.

## Configuration

The narrator model is chosen by configuration with no code change:

1. Owner setting (desktop, local Ollama at `http://127.0.0.1:11434`). Settings stores `model` in `OwnerExplanationSettings` (`PUT /v1/players/{player_id}/explanation-settings`). The name must be listed by `/api/tags`.
2. Operator file. `SCAN64_LLM_CONFIG` names a TOML file read by `LLMProviderConfig.from_toml`. It takes precedence over the owner setting.

For the chosen model, run `ollama pull gemma4:12b`, then pick `gemma4:12b` in Settings, or:

```toml
# file named by SCAN64_LLM_CONFIG
[llm]
provider = "ollama"
model = "gemma4:12b"
base_url = "http://127.0.0.1:11434"
```

For the fallback, render `benchmarks/narrator/modelfiles/praxis-4b.Modelfile` (substitute `@GGUF@`, `@NUM_CTX@`, `@NUM_PREDICT@`, as `--setup` does). Then run `ollama create scan64-bench-praxis-4b -f <rendered Modelfile>` and pick `scan64-bench-praxis-4b:latest`.

Set `SCAN64_LOCAL_LLM_CONTEXT_TOKENS=12288` for the local provider until #388 makes it the default.

## Limits

- Timeout: the app's local-Ollama timeout defaults to 180 s (`SCAN64_LOCAL_LLM_TIMEOUT_SECONDS`, up to 600 s). The benchmark client allowed 120 s; no call came near it (largest p95 4.63 s).
- Context: `num_ctx` must be at least 8192; the benchmark used 12288, the default after #388. At 8192 the 4–5 positions with longer prompts fail as provider errors, so delivered coverage at the current default 8192 is lower than measured here.
- Coverage counts slots: on the slot path a fact counts only when the answer uses a slot that states it. Free-prose coverage counts claim text. The two are close but not the same measure.
- Thinking: every benchmark call sent `think = false`. The app adapters send no `think` field on this branch; that is a follow-up, so production calls of a thinking-capable model can differ from these numbers.
- CPU latency is not measured for every model: only `praxis-4b` and `qwen3.5-4b`, on the pre-#367 prompt (below). The runner now runs without `nvidia-smi` and records null GPU fields.
- One GPU class: all slot and free-prose runs are on an RTX 5090. Latency on smaller GPUs is higher (see the RTX 4080 Laptop run below).
- This measures narrator wording, not chess improvement for players.

## Earlier runs (pre-#367 pipeline only)

These runs used the first six-model shortlist and the prompt before #367 (commit `c589f317`, `assembly.py` sha256 `255ce24b73f9`, `num_ctx` 4096, `num_predict` 1024, no streaming). They do not apply to the current prompt. Config: `benchmarks/narrator/narrator_v1.toml`.

### First survey

Each candidate was checked on Hugging Face on 2026-11-26. The constraint was a model that fits fully in 12 GB VRAM at Q4_K_M with `num_ctx` 4096.

| Model (benchmark name) | Role | Licence | Params (B) | Q4_K_M size (GiB) | Local run | Base model | Training data |
|---|---|---|---|---|---|---|---|
| ChessGPT base v1 (`chessgpt-base`) | chess | Apache-2.0 | 2.8 | 2.26 | GGUF `msj121/chessgpt-base-v1-Q4_K_M-GGUF` (community quant) | RedPajama-INCITE-3B (GPT-NeoX) | `Waterhorse/chess_data`: games, puzzles, annotated games, chess books, forum text |
| Praxis chess reasoner (`praxis-4b`) | chess | Apache-2.0 | 4.2 | 2.76 | GGUF `praxis-chess/praxis-chess-reasoner-qwen3.5-4b-GGUF` with publisher Modelfile | Qwen/Qwen3.5-4B + LoRA | `praxis-chess/praxis-chess-evidence-graphs`: engine evidence graphs with typed claims |
| Qwen3 4B Chess Explain (`qwen3-4b-chess-explain`) | chess | Apache-2.0 | 4.0 | 2.96 | GGUF `HillPhelmuth/Qwen3_4B_Chess_Explain_GGUF_Quants` | Qwen/Qwen3-4B | not stated on the model card |
| ChessLM Qwen3 (`chesslm-qwen3-8b`) | chess | Apache-2.0 | 8.2 | 5.02 | GGUF `VamidkaZ/ChessLM_Qwen3-Q4_K_M-GGUF` | Qwen/Qwen3-8B | `Lichess/chess-puzzles`, RLVR with a Stockfish verifier |
| Qwen3.5 4B (`qwen3.5-4b`) | baseline | Apache-2.0 | 4.7 | 2.92 | Ollama library `qwen3.5:4b` | Qwen/Qwen3.5-4B-Base | general web, code and synthetic data |
| Qwen3 8B (`qwen3-8b`) | baseline | Apache-2.0 | 8.2 | 5.20 | Ollama library `qwen3:8b` | Qwen/Qwen3-8B-Base | general web, code and synthetic data |

Surveyed, not benchmarked:

| Model | Licence | Params (B) | Reason not benchmarked |
|---|---|---|---|
| `Waterhorse/chessgpt-chat-v1` | Apache-2.0 | 2.8 | no GGUF; `chessgpt-base` covers the family and fails the JSON contract |
| Concept-guided chess commentary (CCC) | — | — | no model weights found on Hugging Face |
| `jromanghf/rookstudio-commentary` | Apache-2.0 | 4B class | cap of 6 models; `praxis-4b` is the 4B Qwen3.5 chess candidate |
| `NAKSTStudio/chess-gemma-commentary` | Gemma | 0.27 | far below the 4B to 8B class |
| `Qwen/Qwen3-14B` | Apache-2.0 | 14.8 | does not fit 12 GB at Q4_K_M with `num_ctx` 4096 |
| `meta-llama/Llama-3.1-8B-Instruct` | Llama 3.1 Community | 8.0 | gated; the 8B baseline slot went to `qwen3-8b` |
| `google/gemma-3-12b-it` | Gemma | 12.2 | does not fit 12 GB at Q4_K_M with `num_ctx` 4096 |

Fine-tune option: a LoRA on a small open model, trained on engine-annotated commentary with the Scan64 claim schema as target. The licence of the GameKnot commentary corpus (Jhamtani et al., ACL 2018) must be cleared before any training.

### RTX 5090 (six models)

`summary_20261126_162415.{md,json}`, runs in `runs_20261126_162415.jsonl`.

| Model | Role | Params (B) | Grounding pass | Provider errors | Claims / expl. | False chess statements / expl. | Rejected claims / expl. | SAN legal | SAN correct (SAN tokens) | Latency p50 / p95 (s) | Within 4 s | VRAM (GiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chessgpt-base | chess | 2.8 | 0.0% | 55.3% | 3.79 | 0.00 | 3.79 | — | — (0) | 2.98 / 3.28 | 99.3% | 2.26 |
| praxis-4b | chess | 4.2 | 80.0% | 6.0% | 4.30 | 0.19 | 0.19 | 100.0% | 100.0% (102) | 1.39 / 3.70 | 98.0% | 2.76 |
| qwen3-4b-chess-explain | chess | 4.0 | 33.3% | 4.7% | 3.81 | 1.77 | 2.10 | 91.4% | 76.9% (221) | 0.75 / 3.42 | 100.0% | 2.96 |
| chesslm-qwen3-8b | chess | 8.2 | 22.0% | 0.0% | 6.41 | 0.65 | 3.15 | 97.9% | 97.9% (146) | 1.92 / 3.30 | 98.7% | 5.02 |
| qwen3.5-4b | baseline | 4.7 | 27.3% | 2.0% | 3.69 | 0.24 | 2.61 | 100.0% | 100.0% (68) | 1.56 / 4.02 | 94.7% | 2.92 |
| qwen3-8b | baseline | 8.2 | 1.3% | 67.3% | 8.53 | 0.00 | 8.02 | 100.0% | 100.0% (78) | 4.94 / 5.13 | 26.0% | 5.20 |

The coverage view of these runs is in `rescored_20261126_162415.{md,json}`.

### RTX 4080 Laptop (four models)

`summary_20261126_145813.{md,json}`, NVIDIA GeForce RTX 4080 Laptop GPU 12 GB. Latency only; the qwen3.5-4b and qwen3-8b lines of this runs file ran on the RTX 5090.

| Model | Latency p50 / p95 (s) | Within 4 s |
|---|---|---|
| chessgpt-base | 7.70 / 8.04 | 32.7% |
| praxis-4b | 3.27 / 6.38 | 73.3% |
| qwen3-4b-chess-explain | 1.37 / 8.77 | 76.7% |
| chesslm-qwen3-8b | 5.20 / 8.82 | 12.7% |

### CPU only (two models)

`cpu/summary_20261126_170614.{md,json}`, runs in `runs_20261126_170614.jsonl`, config `benchmarks/narrator/narrator_cpu.toml` (removed in `e318b0b`; CPU runs are out of scope, and the file is in the git history). A separate Ollama 0.40.18 server on 127.0.0.1:11436 started with `CUDA_VISIBLE_DEVICES=-1 OLLAMA_LLM_LIBRARY=cpu`; its device discovery listed only `cpu`, and Ollama reported `size_vram` 0 for both models, so no layer ran on the GPU. CPU: Intel Core i9-14900HX, 32 logical cores. First 10 positions x 1 run, same pipeline and module hash as the GPU runs. The summary header names the GPU present in the machine, not one in use.

| Model | Latency p50 / p95 (s) | Grounding pass (10 calls) |
|---|---|---|
| praxis-4b | 15.19 / 51.86 | 100.0% |
| qwen3.5-4b | 20.92 / 77.71 | 20.0% |

### Validator defect found by the first run

`_tactic` in `validator.py` raised `StopIteration` for claim text with "back-rank" (the regex accepts a hyphen, the `_TACTIC_WORDS` keys use a space). Fixed on `epic/371` in `dddcd85`. The first runs used the pinned pre-fix module.
