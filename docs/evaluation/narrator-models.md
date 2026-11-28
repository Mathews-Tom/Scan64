# Narrator model evaluation (#369, #389)

Question: which open LLM words Scan64 engine evidence best as the narrator? The engine judges. The model only words engine, board and factor facts. The validator decides whether its claims replace the template explanation.

Decision (2026-11-27, factor pipeline): use **`gemma4-e4b-q3km`**, gemma-4-E4B-it at Q3_K_M (`unsloth/gemma-4-E4B-it-GGUF`, pinned revision, Apache-2.0), at `num_ctx` 4096. It needs 2.96–3.13 GiB of GPU memory, so it is also the choice for GPUs under 10 GiB. It is the smallest model near the quality bar; it misses the coverage bar by 2.5 points in the default voice. See [Decision](#decision). This replaces the interim choice of `gemma4-12b` with the `praxis-4b` fallback (slot run `runs_20261127_161413`), which had replaced the 2026-11-26 `praxis-4b` choice measured on the pre-#367 prompt only (on the #367 prompt `praxis-4b` passes grounding in 4.0% of calls; see [Interim slot run](#interim-slot-run-superseded)).

All numbers below are measured. Raw records and summaries are in `benchmarks/narrator/results/`.

## Shortlist (9 models and 2 quantizations)

| Model (benchmark name) | Role | Licence | Params (B) | Source | Base model | GPU memory at `num_ctx` 4096 (GiB) |
|---|---|---|---|---|---|---|
| `praxis-2b` | chess | Apache-2.0 | 2.27 | GGUF `praxis-chess/praxis-chess-reasoner-qwen3.5-2b-GGUF` | Qwen3.5-2B + LoRA (engine evidence graphs) | 2.05 |
| `second-2b` | chess | Apache-2.0 | 1.88 | GGUF `mradermacher/Second-1.0-2B-GGUF` | Qwen3.5-2B, SFT + DAPO on rule-checked Stockfish-fact explanations | 1.88 |
| `qwen3.5-2b` | baseline | Apache-2.0 | 2.27 | Ollama `qwen3.5:2b` | Qwen3.5-2B | 3.42 |
| `praxis-4b` | chess | Apache-2.0 | 4.2 | GGUF `praxis-chess/praxis-chess-reasoner-qwen3.5-4b-GGUF` | Qwen3.5-4B + LoRA (engine evidence graphs) | 3.35 |
| `qwen3.5-4b` | baseline | Apache-2.0 | 4.7 | Ollama `qwen3.5:4b` | Qwen3.5-4B | 4.14 |
| `gemma4-e4b` | baseline | Apache-2.0 | 8.0 (4.5 effective) | Ollama `gemma4:e4b` (Q4_K_M, vision projector, MTP draft model) | google/gemma-4-E4B-it | 4.96 |
| `gemma4-e4b-q4ks` | baseline | Apache-2.0 | 8.0 (4.5 effective) | GGUF `unsloth/gemma-4-E4B-it-GGUF` `gemma-4-E4B-it-Q4_K_S.gguf`, text only | google/gemma-4-E4B-it | 3.53 |
| `gemma4-e4b-q3km` | baseline | Apache-2.0 | 8.0 (4.5 effective) | GGUF `unsloth/gemma-4-E4B-it-GGUF` `gemma-4-E4B-it-Q3_K_M.gguf`, text only | google/gemma-4-E4B-it | 2.96 |
| `qwen3.5-9b` | baseline | Apache-2.0 | 9.65 | Ollama `qwen3.5:9b` | Qwen3.5-9B | 6.67 |
| `granite4.2-8b` | baseline | Apache-2.0 | 8.79 | Ollama `granite4.2:8b` | ibm-granite/granite-4.2-8b | 6.08 |
| `gemma4-12b` | baseline | Apache-2.0 | 11.95 | Ollama `gemma4:12b` | google/gemma-4-12b-it | 8.91 |

Every GGUF is pinned by repository revision and sha256 in `benchmarks/narrator/narrator.toml`. Library models are pinned by the `ollama_digest` in each runs file. GPU memory is the measured rise in `nvidia-smi` memory when the model loads (weights, KV cache and buffers), from `runs_20261127_213350`. Ollama's `size_vram` under-reports the gemma4 models.

The two quantizations use the chat format and sampling of the library model `gemma4:e4b` (`RENDERER gemma4`, `PARSER gemma4`, temperature 1, top_k 64, top_p 0.95). They load no vision projector and no MTP draft model.

Dropped from the first shortlist: `chessgpt-base`, `qwen3-4b-chess-explain`, `chesslm-qwen3-8b`, `qwen3-8b` (see [Earlier runs](#earlier-runs-pre-367-pipeline-only)). `Caissa-Chess-M1` was dropped because it is a chain-of-thought reasoning model; the narrator must not reason.

## Quality bar

The bar was stated before the factor run. Rule (Sydney, 2026-11-27): choose the **smallest** model that meets the bar, not the top scorer. "A small model working fairly is better than a large one working nicely."

| Criterion | Bar |
|---|---|
| Delivered coverage (default voice `coach`, Caissa evidence) | ≥ 60% |
| Grounding pass | ≥ 70% |
| Calls that thought | 0 |
| Latency p95 on the RTX 5090 | ≤ 3 s |
| GPU memory at the chosen `num_ctx` | ≤ 4 GiB (a 6 GB card with room for the desktop) |

Procedure: rank the models that pass by GPU memory, smallest first, and choose the first. If no model passes within 4 GiB, the smallest passing model is the 8 GB-tier choice. The best-scoring model is named only as a comparison. GPU memory is measured with the compact prompt (#408) at `num_ctx` 4096, because the longest prompt plus 2,048 output tokens fits 4096 (see [Prompt size](#prompt-size-and-num_ctx)).

## Method

- Commands, repository root, Ollama 0.40.18 on 127.0.0.1:11435:
  - main run: `uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml --positions-file benchmarks/narrator/positions-caissa.json --num-ctx 4096`;
  - engine comparison: the same with `--positions-file benchmarks/narrator/positions.json --models ...`;
  - voices: the same with `--voice terse` or `--voice playful --models ...`.
- Positions: `positions-caissa.json`, the 32 of the 50 benchmark positions that Caissa 2.0 diagnoses with a trusted code (#396; same games and learner moves). Each model runs 3 times per position (96 calls per model). The engine comparison uses `positions.json`, the 50 positions from recorded Stockfish 19 searches (150 calls per model).
- Pipeline: the production `scan64.explanations.assembly:resolve_explanation` with an `OllamaExplanationAdapter` from `create_llm_provider`. It has fact slots (#386), factor facts and factor slots (#408), the fact-driven vocabulary rule (#408), the slot-list-only compact prompt (#408), fitted factor thresholds (#410) and line evaluations (#367).
- Request: `think = false` and `stream = true` in every `/api/chat` body. `num_ctx` 4096 in every request, `num_predict` 2048. Client timeout 120 s.
- Hardware: NVIDIA GeForce RTX 5090 32 GB. No other Ollama server held a model during any call.
- Every runs header: `assembly.py` sha256 `79c006f90a10`, runner sha256 `3649f46a330a`, `dirty: false`. The header commits (`29f0424`, `ecfc8de`, `937353f`) differ only in committed result files.
- Later runs: the header's `pipeline` also records `config_file` and `config_sha256`, the SHA-256 of the config file (`narrator.toml` or the file given on the command line), and `--resume` refuses a run whose config file has changed. It also records `narrator_sha256`, one SHA-256 over every file of the narrator package `src/scan64/explanations` (assembly, slots, facts, factor_facts, validator, board_lines, voice, the factor port and the templates; `narrator_modules` lists each file's SHA-256), and `--resume` refuses a run whose narrator package has changed. `module_sha256` covers `assembly.py` only. The runs below predate both fields.

| Runs file | Positions | Voice | Models |
|---|---|---|---|
| `runs_20261127_213350` | Caissa (32) | coach | 9 + 2 quantizations |
| `runs_20261127_220133` | Caissa (32) | coach | repeat of the 4 gemma variants |
| `runs_20261127_221105` | Stockfish 19 (50) | coach | top 3 of the 9 + `gemma4-e4b-q3km` |
| `runs_20261127_222432` | Caissa (32) | terse | the same 4 |
| `runs_20261127_223247` | Caissa (32) | playful | the same 4 |

`gemma4-e4b-q3km` is in the last three runs as the candidate nearest the bar, next to the top 3 of the 9 by delivered coverage.

### Metrics

| Metric | Definition |
|---|---|
| Grounding pass | Share of calls where the pipeline accepts the model output instead of the template. |
| Coverage (accepted) | Share of three key engine facts (played move, key move, result) that an accepted answer states. On the slot path it counts only the slots the answer uses (`slot_coverage`). |
| Delivered coverage | Coverage over all calls; a fallback counts as 0. This is what a learner gets. |
| Full coverage | Share of all calls accepted with all three key facts. |
| Names a factor (accepted / where available) | Share of accepted answers that use at least one listed `{factor:...}` slot, a measured factor change (#410). The second value counts only positions whose slot list has a factor slot: 20 of the 32 Caissa positions, 33 of the 50 Stockfish positions. Runs recorded before the factor slots show `—`. |
| False chess statements / expl. | Claims rejected for a chess reason, per explanation. On the slot path this includes `chess_token_outside_slot`. |
| Latency p50 / p95 | Wall time of the provider call. |
| First token p50 | Time to the first streamed token. |
| Hit output cap | Share of calls that stop at `num_predict` (`done_reason = length`). |
| Calls that thought | Calls with thinking text or a `<think>` tag in the answer. Must be 0. |
| Prompt tokens median / max | `prompt_eval_count` per call. |
| GPU memory | Measured load rise in `nvidia-smi`; null without `nvidia-smi`. |

## Results

### Factor pipeline, coach voice, Caissa evidence

`summary_20261127_213350.{md,json}`, 96 calls per model. Calls that thought: 0 for every model.

| Model | Grounding pass | Coverage (accepted) | Delivered coverage | Full coverage | Names a factor (accepted / where available) | False chess statements / expl. | Latency p50 / p95 (s) | First token p50 (s) | Hit output cap | Calls that thought | Prompt tokens median / max | GPU memory (GiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma4-12b | 74.0% | 84.0% | 62.2% | 41.7% | 59.2% / 100.0% | 0.19 | 1.70 / 3.12 | 0.39 | 0.0% | 0 | 1258 / 1565 | 8.91 |
| gemma4-e4b-q3km | 91.7% | 63.6% | 58.3% | 29.2% | 52.3% / 80.7% | 0.06 | 0.61 / 1.33 | 0.07 | 0.0% | 0 | 1254 / 1561 | 2.96 |
| gemma4-e4b-q4ks | 79.2% | 68.9% | 54.5% | 26.0% | 47.4% / 73.5% | 0.23 | 1.15 / 2.15 | 0.07 | 0.0% | 0 | 1254 / 1561 | 3.53 |
| gemma4-e4b | 80.2% | 65.8% | 52.8% | 26.0% | 42.9% / 73.3% | 0.25 | 0.70 / 1.26 | 0.07 | 0.0% | 0 | 1254 / 1561 | 4.96 |
| praxis-4b | 50.0% | 68.8% | 34.4% | 18.8% | 100.0% / 100.0% | 0.50 | 0.64 / 7.06 | 0.09 | 18.8% | 0 | 1223 / 1527 | 3.35 |
| qwen3.5-9b | 53.1% | 48.4% | 25.7% | 13.5% | 45.1% / 67.6% | 0.77 | 1.28 / 2.78 | 0.09 | 0.0% | 0 | 1223 / 1527 | 6.67 |
| qwen3.5-4b | 52.1% | 37.3% | 19.4% | 9.4% | 28.0% / 46.7% | 0.69 | 0.74 / 1.75 | 0.10 | 0.0% | 0 | 1223 / 1527 | 4.14 |
| granite4.2-8b | 44.8% | 38.8% | 17.4% | 8.3% | 25.6% / 45.8% | 0.93 | 1.50 / 2.66 | 0.12 | 0.0% | 0 | 1243 / 1565 | 6.08 |
| second-2b | 4.2% | 41.7% | 1.7% | 0.0% | 25.0% / 50.0% | 0.90 | 0.61 / 1.27 | 0.06 | 2.1% | 0 | 1223 / 1527 | 1.88 |
| qwen3.5-2b | 2.1% | 33.3% | 0.7% | 0.0% | 50.0% / 50.0% | 1.80 | 1.14 / 2.23 | 0.05 | 0.0% | 0 | 1223 / 1527 | 3.42 |
| praxis-2b | 0.0% | — | 0.0% | 0.0% | — / — | 2.33 | 0.69 / 4.47 | 0.06 | 25.0% | 0 | 1223 / 1527 | 2.05 |

### Against the bar

The 7 other models fail the bar by far: delivered coverage 0–34% and grounding pass 0–53% (table above). The four gemma variants are near it, so they ran twice (`runs_20261127_220133` is the repeat). Pooled over 192 calls each:

| Model | Delivered coverage (run 1 / run 2 / pooled) | Grounding pass (pooled) | Calls that thought | p95 pooled (s) | GPU memory (GiB) | Quality bar | Within 4 GiB |
|---|---|---|---|---|---|---|---|
| gemma4-e4b-q3km | 58.3% / 56.6% / **57.5%** | 93.8% | 0 | 1.31 | 2.96–3.13 | fail: coverage | yes |
| gemma4-e4b-q4ks | 54.5% / 51.4% / **53.0%** | 78.1% | 0 | 2.16 | 3.53–3.53 | fail: coverage | yes |
| gemma4-e4b | 52.8% / 60.4% / **56.6%** | 83.9% | 0 | 1.16 | 4.96–5.14 | fail: coverage | no |
| gemma4-12b | 62.2% / 60.1% / **61.1%** | 72.4% | 0 | 3.10 | 8.91–8.91 | fail: p95 | no |

No model meets the full bar. Two runs of one model differ by up to 7.6 points in delivered coverage (`gemma4-e4b`), so the 2.5-point gap of `gemma4-e4b-q3km` is within run-to-run noise. `gemma4-12b`, the best-scoring model, misses p95 by 0.10 s and needs 8.91 GiB.

### Prompt size and num_ctx

| Pipeline (runs file) | Prompt tokens median | Prompt tokens max | Max + 2,048 output | Smallest window that fits |
|---|---|---|---|---|
| Free prose, #367 (`runs_20261126_230005`) | 4,052 | 6,531 | 8,579 | 12288 |
| Interim slots, #386 (`runs_20261127_161413`) | 6,164 | 9,869 | 11,917 | 12288 |
| Factor pipeline, compact prompt, #408 (`runs_20261127_213350`) | 1,227 | 1,565 | 3,613 | **4096** |

Per model (factor run), median / max prompt tokens: 1,223 / 1,527 for the Qwen3.5-based models (both praxis, second-2b, qwen3.5); 1,243 / 1,565 for `granite4.2-8b`; 1,254 / 1,561 for the gemma4-e4b variants; 1,258 / 1,565 for `gemma4-12b`. On the Stockfish positions the maximum is 1,476. GPU memory at 4096 for every model is in the shortlist table. No call failed for the context: every provider error is an output-cap hit (`done_reason = length`).

Recommendation: lower the local default `num_ctx` from 12288 to **4096** (`SCAN64_LOCAL_LLM_CONTEXT_TOKENS`, `DEFAULT_OLLAMA_CONTEXT_TOKENS`). The longest lesson prompt plus 2,048 output tokens is 3,613 tokens. The longest Analysis Board prompt (MultiPV 4) is 1,608 tokens ([narrator pipeline](../narrator-pipeline.md), prompt size), so it needs 3,656.

### Quantization variants of gemma4-e4b

Same runs, pooled over the two coach runs on Caissa evidence (192 calls each):

| Variant | File (GiB) | GPU memory at 4096 (GiB) | Grounding pass | Delivered coverage | False chess statements / expl. (run 1) | Latency p50 / p95 (s) (run 1) |
|---|---|---|---|---|---|---|
| `gemma4-e4b` library Q4_K_M (+ projector, MTP draft) | 6.13 | 4.96–5.14 | 83.9% | 56.6% | 0.25 | 0.70 / 1.26 |
| `gemma4-e4b-q4ks` Q4_K_S, text only | 4.51 | 3.53 | 78.1% | 53.0% | 0.23 | 1.15 / 2.15 |
| `gemma4-e4b-q3km` Q3_K_M, text only | 3.78 | 2.96–3.13 | **93.8%** | **57.5%** | **0.06** | 0.61 / 1.33 |

Q3_K_M loses no measured quality against Q4_K_M: its grounding pass is higher and its delivered coverage is equal within noise. It saves about 2 GiB. The library model is faster per token through its MTP draft model; Q4_K_S has no draft model and is the slowest of the three.

### Factor metric (#410)

"Names a factor" counts accepted answers that use a listed `{factor:...}` slot. Where the position has a factor slot, `gemma4-12b` names one in 97–100% of accepted answers, the gemma4-e4b variants in 73–96%, `praxis-4b` in 100%. Over all accepted answers the share is 43–63% for the gemma variants, because 12 of the 32 Caissa positions have no measured factor change. No factor slot is invented: a factor slot that the position does not have fails as `unknown_slot`.

### Three pipelines side by side

Free prose (#367, `runs_20261126_230005`, Stockfish evidence, 150 calls), interim slots (#386, `runs_20261127_161413`, Stockfish evidence, 150 calls) and the factor pipeline (`runs_20261127_213350`, Caissa evidence, 96 calls). The evidence engine differs in the last column; the next section shows its effect.

| Model | Grounding pass: prose / slots / factors | Delivered coverage: prose / slots / factors | Full coverage: prose / slots / factors | False chess statements / expl.: prose / slots / factors | Latency p95 (s): prose / slots / factors | GPU memory (GiB): 12288 / 12288 / 4096 |
|---|---|---|---|---|---|---|
| gemma4-12b | 78.7% / 46.0% / **74.0%** | 72.7% / 41.3% / **62.2%** | 60.7% / 34.7% / 41.7% | 0.17 / 0.57 / 0.19 | 2.42 / 2.52 / 3.12 | 9.43 / 9.43 / 8.91 |
| gemma4-e4b-q3km | — / — / **91.7%** | — / — / **58.3%** | — / — / 29.2% | — / — / 0.06 | — / — / 1.33 | — / — / 2.96 |
| gemma4-e4b-q4ks | — / — / **79.2%** | — / — / **54.5%** | — / — / 26.0% | — / — / 0.23 | — / — / 2.15 | — / — / 3.53 |
| gemma4-e4b | 84.0% / 45.3% / **80.2%** | 51.1% / 33.8% / **52.8%** | 20.0% / 21.3% / 26.0% | 0.06 / 0.68 / 0.25 | 0.80 / 1.12 / 1.26 | 5.28 / 5.28 / 4.96 |
| praxis-4b | 4.0% / 44.0% / **50.0%** | 2.7% / 36.7% / **34.4%** | 0.0% / 24.0% / 18.8% | 0.00 / 0.92 / 0.50 | 7.76 / 1.72 / 7.06 | 3.70 / 3.67 / 3.35 |
| qwen3.5-9b | 44.0% / 12.7% / **53.1%** | 34.9% / 9.8% / **25.7%** | 23.3% / 6.7% / 13.5% | 0.90 / 1.69 / 0.77 | 2.92 / 4.37 / 2.78 | 7.05 / 7.01 / 6.67 |
| qwen3.5-4b | 47.3% / 34.7% / **52.1%** | 37.1% / 23.6% / **19.4%** | 24.0% / 14.0% / 9.4% | 0.53 / 1.05 / 0.69 | 2.32 / 2.24 / 1.75 | 4.49 / 4.49 / 4.14 |
| granite4.2-8b | 60.0% / 25.3% / **44.8%** | 55.1% / 14.7% / **17.4%** | 46.7% / 7.3% / 8.3% | 0.46 / 1.42 / 0.93 | 2.05 / 2.99 / 2.66 | 7.46 / 7.48 / 6.08 |
| second-2b | 34.0% / 0.0% / **4.2%** | 22.2% / 0.0% / **1.7%** | 7.3% / 0.0% / 0.0% | 0.33 / 1.53 / 0.90 | 0.81 / 1.22 / 1.27 | 2.05 / 2.06 / 1.88 |
| qwen3.5-2b | 12.7% / 2.0% / **2.1%** | 8.2% / 1.6% / **0.7%** | 4.0% / 1.3% / 0.0% | 1.91 / 2.94 / 1.80 | 2.80 / 4.51 / 2.23 | 3.58 / 3.58 / 3.42 |
| praxis-2b | 0.0% / 0.0% / **0.0%** | 0.0% / 0.0% / **0.0%** | 0.0% / 0.0% / 0.0% | 0.50 / 3.12 / 2.33 | 4.73 / 4.63 / 4.47 | 2.05 / 2.05 / 2.05 |

The factor pipeline recovers most of the loss of the interim slot run. `gemma4-12b` goes from 41.3% to 62.2% delivered coverage, `gemma4-e4b` from 33.8% to 52.8%. False chess statements fall to the free-prose level. The vocabulary rule (#408) allows the words of the mistake class and of the passed factors, so true statements such as "missed a direct threat" no longer fail. The small Qwen3.5 models and both praxis models stay low.

### Caissa against Stockfish evidence (#396)

Same pipeline, commit and settings; `positions-caissa.json` (Caissa 2.0, 32 positions) against `positions.json` (recorded Stockfish 19 searches, 50 positions).

| Model | Evidence | Positions | Grounding pass | Delivered coverage | Full coverage | Names a factor (accepted / where available) | False chess statements / expl. | Latency p95 (s) |
|---|---|---|---|---|---|---|---|---|
| gemma4-12b | Caissa 2.0 | 32 | 74.0% | 62.2% | 41.7% | 59.2% / 100.0% | 0.19 | 3.12 |
| gemma4-12b | Stockfish 19 (recorded) | 50 | 70.7% | 62.4% | 48.7% | 63.2% / 100.0% | 0.18 | 2.65 |
| gemma4-e4b | Caissa 2.0 | 32 | 80.2% | 52.8% | 26.0% | 42.9% / 73.3% | 0.25 | 1.26 |
| gemma4-e4b | Stockfish 19 (recorded) | 50 | 88.0% | 62.9% | 33.3% | 56.1% / 83.1% | 0.11 | 1.15 |
| gemma4-e4b-q3km | Caissa 2.0 | 32 | 91.7% | 58.3% | 29.2% | 52.3% / 80.7% | 0.06 | 1.33 |
| gemma4-e4b-q3km | Stockfish 19 (recorded) | 50 | 96.7% | 66.4% | 37.3% | 56.6% / 84.5% | 0.03 | 0.82 |
| praxis-4b | Caissa 2.0 | 32 | 50.0% | 34.4% | 18.8% | 100.0% / 100.0% | 0.50 | 7.06 |
| praxis-4b | Stockfish 19 (recorded) | 50 | 38.0% | 26.0% | 14.0% | 94.7% / 100.0% | 0.76 | 7.04 |

- The narrator words Caissa evidence about as well as Stockfish evidence for `gemma4-12b` (62.2% against 62.4%).
- The gemma4-e4b variants lose 8–10 points of delivered coverage on Caissa evidence. The position sets differ: Caissa diagnoses 32 of 50 positions, with fewer missed_check and missed_direct_threat positions (#396). A same-position comparison needs Caissa diagnoses for the same 32 positions on both sides.
- Prompts are shorter on Stockfish evidence (max 1,476 against 1,561 tokens).
- On Stockfish evidence `gemma4-e4b-q3km` passes the bar: 66.4% delivered coverage, 96.7% grounding pass, p95 0.82 s.

### Voices (#387), Caissa evidence

`coach` is `runs_20261127_213350`; `terse` is `runs_20261127_222432`; `playful` is `runs_20261127_223247`. 96 calls per row.

| Model | Voice | Grounding pass | Delivered coverage | Full coverage | Names a factor (accepted) | False chess statements / expl. | Latency p50 / p95 (s) | Output tokens (mean) | Calls that thought |
|---|---|---|---|---|---|---|---|---|---|
| gemma4-12b | coach | 74.0% | 62.2% | 41.7% | 59.2% | 0.19 | 1.70 / 3.12 | 448 | 0 |
| gemma4-12b | terse | 72.9% | 64.2% | 49.0% | 57.1% | 0.16 | 1.58 / 2.45 | 378 | 0 |
| gemma4-12b | playful | 58.3% | 49.0% | 33.3% | 51.8% | 0.22 | 1.75 / 3.24 | 464 | 0 |
| gemma4-e4b | coach | 80.2% | 52.8% | 26.0% | 42.9% | 0.25 | 0.70 / 1.26 | 247 | 0 |
| gemma4-e4b | terse | 90.6% | 60.8% | 32.3% | 55.2% | 0.07 | 0.43 / 0.93 | 181 | 0 |
| gemma4-e4b | playful | 65.6% | 41.7% | 18.8% | 49.2% | 0.42 | 0.82 / 1.30 | 283 | 0 |
| gemma4-e4b-q3km | coach | 91.7% | 58.3% | 29.2% | 52.3% | 0.06 | 0.61 / 1.33 | 114 | 0 |
| gemma4-e4b-q3km | terse | 97.9% | 61.8% | 33.3% | 57.4% | 0.02 | 0.59 / 1.24 | 109 | 0 |
| gemma4-e4b-q3km | playful | 93.8% | 58.3% | 32.3% | 51.1% | 0.06 | 0.63 / 1.81 | 139 | 0 |
| praxis-4b | coach | 50.0% | 34.4% | 18.8% | 100.0% | 0.50 | 0.64 / 7.06 | 494 | 0 |
| praxis-4b | terse | 53.1% | 35.4% | 18.8% | 88.2% | 0.63 | 0.63 / 7.04 | 439 | 0 |
| praxis-4b | playful | 56.2% | 34.4% | 12.5% | 77.8% | 0.59 | 0.62 / 6.89 | 437 | 0 |

Mean delivered coverage over the three voices: `gemma4-e4b-q3km` 59.5%, `gemma4-12b` 58.5%, `gemma4-e4b` 51.8%, `praxis-4b` 34.7%. `gemma4-e4b-q3km` varies least between voices (58.3–61.8%) and passes the coverage bar in `terse`. `playful` costs `gemma4-12b` 13 points and `gemma4-e4b` 11 points.

### Why factor-pipeline answers fall back

Fallback reasons, main run (calls out of 96):

| Model | `chess_token_outside_slot` | `unknown_slot` | `malformed_line_move` | `unknown_evidence` | `provider_error` (output cap) |
|---|---|---|---|---|---|
| gemma4-12b | 13 | 11 | 1 | 0 | 0 |
| gemma4-e4b-q3km | 5 | 2 | 1 | 0 | 0 |
| gemma4-e4b-q4ks | 18 | 2 | 0 | 0 | 0 |
| gemma4-e4b | 17 | 2 | 0 | 0 | 0 |
| praxis-4b | 0 | 3 | 27 | 0 | 18 |
| qwen3.5-4b | 37 | 1 | 8 | 0 | 0 |
| second-2b | 36 | 0 | 12 | 42 | 2 |
| praxis-2b | 66 | 0 | 6 | 0 | 24 |

Accepted answers lose coverage mostly on the key move. For a hanging piece the key move is the opponent's capture, but answers often name only the best move, for example "You played Rxd2, but h6 was stronger. That was a Hanging piece: after your move, threats fell by 0.75 pawns for you." (`gemma4-e4b-q3km`).

## Decision

2026-11-27: the narrator model is **`gemma4-e4b-q3km`** at `num_ctx` 4096, on every GPU with at least 4 GiB free. It is also the choice for GPUs under 10 GiB; no separate fallback is needed. Below about 3.5 GiB free, no measured model works and the template explanation stays.

No model meets the full bar, so the choice is the smallest model nearest to it:

| Criterion (bar) | `gemma4-e4b-q3km` (chosen) | `gemma4-12b` (best score, comparison only) |
|---|---|---|
| Delivered coverage, coach, Caissa (≥ 60%) | 57.5% pooled (58.3%, 56.6%): **misses by 2.5 points** | 61.1% pooled: passes |
| Grounding pass (≥ 70%) | 93.8%: passes | 72.4%: passes |
| Calls that thought (0) | 0: passes | 0: passes |
| Latency p95 on the 5090 (≤ 3 s) | 1.31 s: passes | 3.10 s: **misses by 0.10 s** |
| GPU memory at `num_ctx` 4096 (≤ 4 GiB) | 2.96–3.13 GiB: passes | 8.91 GiB: **fails** |
| Delivered coverage, voice mean | 59.5% | 58.5% |
| Delivered coverage, Stockfish evidence | 66.4% | 62.4% |
| False chess statements / expl., coach | 0.06 | 0.19 |

- `gemma4-e4b-q3km` passes four of five criteria. Its coverage gap (2.5 points) is smaller than the run-to-run spread (up to 7.6 points). It passes the coverage bar in `terse` and on Stockfish evidence.
- It has the highest grounding pass and the fewest false chess statements of all 11 models.
- `gemma4-12b` also fails the bar (p95 and memory), and it needs a GPU with more than 10 GiB free.
- No model passes the full bar within 4 GiB, and none passes outside it. So there is no 8 GB-tier choice that differs from the chosen model.

The rule for a later change stays: rerun this benchmark when the prompt builder, the slot rules, the validator or a candidate changes. A smaller model replaces `gemma4-e4b-q3km` when it meets the bar. A larger model replaces it only when `gemma4-e4b-q3km` misses the bar and the larger model meets it.

## Follow-ups

- Coverage bar: answers miss the key move most often (the opponent's capture for a hanging piece). A prompt line or example that asks for the key-move slot can close the 2.5-point gap.
- ~~`num_ctx` default: 4096 fits every lesson and Analysis Board prompt (above). Lower `DEFAULT_OLLAMA_CONTEXT_TOKENS` to 4096.~~ Done in #429.
- ~~Bundling: the chosen model is a GGUF with a Modelfile, not a library tag. The app needs a setup step that downloads the pinned file and runs `ollama create`, or a published Ollama tag.~~ Done in #429: `scan64-cli narrator install`.
- Engine comparison on the same positions: Caissa diagnoses 32 of 50 positions. A comparison on the same 32 positions on both engines removes the position-set difference.
- ~~`think = false` in production: the app adapters send no `think` field. The benchmark numbers assume it.~~ Done in #429: both Ollama paths send `think: false`.
- `praxis-4b` loops to the output cap in 18 of 96 calls (p95 7.06 s) and writes malformed line moves in 27.
- `benchmarks/narrator/HANDOFF-5090.md` is a machine handoff note, not a benchmark file. It should leave the repository; it stays on this branch because file deletion is outside this terminal's policy.

## Configuration

The narrator model is chosen by configuration with no code change:

1. Owner setting (desktop, local Ollama at `http://127.0.0.1:11434`). Settings stores `model` in `OwnerExplanationSettings` (`PUT /v1/players/{player_id}/explanation-settings`). The name must be listed by `/api/tags`.
2. Operator file. `SCAN64_LLM_CONFIG` names a TOML file read by `LLMProviderConfig.from_toml`. It takes precedence over the owner setting.

For the chosen model, `scan64-cli narrator install` (#429) does steps 1 to 3 after checking Ollama and 4 GiB of free GPU memory ([user guide](../user-guide.md#the-recommended-narrator-model)). By hand:

1. Download `gemma-4-E4B-it-Q3_K_M.gguf` from `unsloth/gemma-4-E4B-it-GGUF` at revision `bfc15c382204943c3a8fff0c750b94ae2364d7a3`. Check its sha256 `0980898d0e7d9c75cea05141bf4010393312101ef6cf5b31f8197e0de18655f4`. `--setup --models gemma4-e4b-q3km` does both steps.
2. Render `benchmarks/narrator/modelfiles/gemma4-e4b-q3km.Modelfile`: substitute `@GGUF@`, `@NUM_CTX@` (4096) and `@NUM_PREDICT@` (2048).
3. Run `ollama create scan64-narrator-gemma4-e4b-q3km -f <rendered Modelfile>`.
4. Pick `scan64-narrator-gemma4-e4b-q3km:latest` in Settings, or use the TOML below.

```toml
# file named by SCAN64_LLM_CONFIG
[llm]
provider = "ollama"
model = "scan64-narrator-gemma4-e4b-q3km:latest"
base_url = "http://127.0.0.1:11434"
context_tokens = 4096
```

Since #429, 4096 is the default `SCAN64_LOCAL_LLM_CONTEXT_TOKENS` and `llm.context_tokens`, so neither needs setting.

## Limits

- Sample size: 96 calls per model on the 32 Caissa positions (192 for the four gemma variants). Run-to-run spread is up to 7.6 points of delivered coverage, larger than the 2.0 points seen at 150 calls in the interim run.
- Position sets: the Caissa set has 32 positions, the Stockfish set 50, so the engine columns compare different positions.
- Coverage counts slots: a fact counts only when the answer uses a slot that states it. Free-prose coverage counts claim text. The two are close but not the same measure.
- Timeout: the app's local-Ollama timeout defaults to 180 s (`SCAN64_LOCAL_LLM_TIMEOUT_SECONDS`, up to 600 s). The benchmark client allowed 120 s; the largest p95 was 7.06 s.
- Thinking: every benchmark call sent `think = false`. Before #429 the app adapters sent no `think` field, so production calls of a thinking-capable model could differ from these numbers; since #429 both Ollama paths send `think: false`.
- One GPU class: all factor, slot and free-prose runs are on an RTX 5090. Latency on smaller GPUs is higher (see the RTX 4080 Laptop run below). CPU latency is measured only for `praxis-4b` and `qwen3.5-4b`, on the pre-#367 prompt.
- Provenance of the five runs above (`runs_20261127_213350`, `_220133`, `_221105`, `_222432`, `_223247`): their headers predate `config_sha256` and `narrator_sha256`, so they cannot be resumed and the header alone does not name the `narrator.toml` or the narrator modules that ran. Both follow from git, because every header has `dirty: false`: `benchmarks/narrator/narrator.toml` has not changed since `29f0424` (`git log -- benchmarks/narrator/narrator.toml`; sha256 `7f109df11a1a` at all three header commits), and `src/scan64/explanations` is identical at `29f0424`, `ecfc8de` and `937353f` (`narrator_sha256` as the runner now computes it: `792cd8a8b563`, 15 files).
- This measures narrator wording, not chess improvement for players.

## Interim slot run (superseded)

The runs below were measured on the slot pipeline before the factor facts, the vocabulary rule and the compact prompt (#408), with `num_ctx` 12288 and Stockfish evidence. They are kept for comparison. Their decision (`gemma4-12b`, fallback `praxis-4b`) is replaced by the [Decision](#decision) above.

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
