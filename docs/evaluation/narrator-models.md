# Narrator model evaluation (#369)

> **2026-11-26 rerun on the #367 prompt: the recommendation below is out of date.** The rerun ([`summary_20261126_230005`](../../benchmarks/narrator/results/summary_20261126_230005.md), 9 models, RTX 5090) gives `praxis-4b` a 4.0% grounding pass; 56% of its calls hit the output cap, and its 0.00 false chess statements come from answers that never parse. By delivered coverage the leaders are `gemma4-12b` (78.7% grounding pass, 72.7% delivered coverage), `granite4.2-8b` (60.0%, 55.1%) and `gemma4-e4b` (84.0%, 51.1%). The new model decision, and a replacement for the rule in [Decision](#decision), belong to #389. The text below describes the pre-#367 runs.

Question: does an open chess-aware LLM make fewer false chess statements than a general-purpose LLM of the same size when it puts Scan64 engine evidence into words? The engine judges. The model only words engine and board facts, and the #366 validator decides whether its claims replace the template explanation.

Answer: yes for one candidate. `praxis-4b` passes the grounding check in 80.0% of calls on the RTX 5090. Its same-size general baseline `qwen3.5-4b` passes in 27.3%. `qwen3-4b-chess-explain` passes in 33.3% but makes 1.77 false chess statements per explanation against the baseline's 0.24; `chesslm-qwen3-8b` and `chessgpt-base` pass less often than the baseline. See [Recommendation](#recommendation).

All numbers below are measured. Raw records and summaries are in `benchmarks/narrator/results/`.

## Survey

Each candidate was checked on Hugging Face (model API and model card) on 2026-11-26. "Local run" means a GGUF that Ollama loads. The benchmark constraint was a model that fits fully in 12 GB VRAM at Q4_K_M with `num_ctx` 4096, so models up to about 8B parameters.

### Benchmarked (6)

| Model (benchmark name) | Role | Licence | Params (B) | Q4_K_M size (GiB) | Local run | Base model | Training data |
|---|---|---|---|---|---|---|---|
| ChessGPT base v1 (`chessgpt-base`) | chess | Apache-2.0 | 2.8 | 2.26 | GGUF `msj121/chessgpt-base-v1-Q4_K_M-GGUF` (community quant) | RedPajama-INCITE-3B (GPT-NeoX) | `Waterhorse/chess_data`: games, puzzles, annotated games, chess books, forum text |
| Praxis chess reasoner (`praxis-4b`) | chess | Apache-2.0 | 4.2 | 2.76 | GGUF `praxis-chess/praxis-chess-reasoner-qwen3.5-4b-GGUF` with publisher Modelfile | Qwen/Qwen3.5-4B + LoRA (`praxis-chess/praxis-chess-reasoner-qwen3.5-4b-lora`) | `praxis-chess/praxis-chess-evidence-graphs`: engine evidence graphs with typed claims |
| Qwen3 4B Chess Explain (`qwen3-4b-chess-explain`) | chess | Apache-2.0 | 4.0 | 2.96 | GGUF `HillPhelmuth/Qwen3_4B_Chess_Explain_GGUF_Quants` | Qwen/Qwen3-4B (`unsloth/Qwen3-4B-unsloth-bnb-4bit`) | not stated on the model card |
| ChessLM Qwen3 (`chesslm-qwen3-8b`) | chess | Apache-2.0 | 8.2 | 5.02 | GGUF `VamidkaZ/ChessLM_Qwen3-Q4_K_M-GGUF` (quant of `karanps/ChessLM_Qwen3`) | Qwen/Qwen3-8B | `Lichess/chess-puzzles`, RLVR with a Stockfish verifier |
| Qwen3.5 4B (`qwen3.5-4b`) | baseline | Apache-2.0 | 4.7 | 2.92 | Ollama library `qwen3.5:4b` | Qwen/Qwen3.5-4B-Base | general web, code and synthetic data (Qwen report) |
| Qwen3 8B (`qwen3-8b`) | baseline | Apache-2.0 | 8.2 | 5.20 | Ollama library `qwen3:8b` | Qwen/Qwen3-8B-Base | general web, code and synthetic data (Qwen report) |

Every GGUF file is pinned by repository revision and sha256 in `benchmarks/narrator/narrator.toml`. Sizes are the Ollama-reported model sizes from the 5090 run.

`qwen3.5-4b` is the same-size baseline for the best chess candidate: `praxis-4b` is a LoRA on the same Qwen3.5-4B weights, so the comparison isolates the fine-tune. `qwen3-8b` is the same-size baseline for `chesslm-qwen3-8b`.

### Surveyed, not benchmarked

| Model | Licence | Params (B) | Local run | Base model / training data | Reason not benchmarked |
|---|---|---|---|---|---|
| `Waterhorse/chessgpt-chat-v1` | Apache-2.0 | 2.8 (ChessGPT base architecture) | no GGUF on the model repo | ChessGPT base; `Waterhorse/chess_data` plus ShareGPT, OpenAssistant oasst1, alpaca-gpt4 | `chessgpt-base` covers the ChessGPT family; it fails the JSON contract (see results) |
| Concept-guided chess commentary (CCC) | — | — | — | — | no CCC model weights found on Hugging Face (searches "concept-guided", "ccc-chess", "chess-commentary") |
| `jromanghf/rookstudio-commentary` | Apache-2.0 | 4B class | GGUF | Qwen/Qwen3.5-4B | cap of 6 models; `praxis-4b` is the 4B Qwen3.5 chess candidate trained on engine evidence |
| `NAKSTStudio/chess-gemma-commentary` | Gemma | 0.27 | GGUF | not stated | far below the 4B to 8B class of the other candidates |
| `Qwen/Qwen3-14B` | Apache-2.0 | 14.8 | Ollama library | Qwen3-14B-Base | does not fit 12 GB at Q4_K_M with `num_ctx` 4096 |
| `meta-llama/Llama-3.1-8B-Instruct` | Llama 3.1 Community | 8.0 | Ollama library | Meta-Llama-3.1-8B | gated; the 8B baseline slot went to `qwen3-8b`, the base of `chesslm-qwen3-8b` |
| `google/gemma-3-12b-it` | Gemma | 12.2 | Ollama library | gemma-3-12b-pt | does not fit 12 GB at Q4_K_M with `num_ctx` 4096 |

### Fine-tune option

The alternative to an existing chess model is a LoRA on a small open model (for example Qwen3.5-4B) trained on engine-annotated commentary: human move commentary (the GameKnot commentary corpus of Jhamtani et al., ACL 2018) paired with Stockfish lines and evaluations for the same positions, and Scan64's own evidence payloads as input. The target output is the Scan64 claim schema, so training rewards claims that cite evidence ids and stay within the stated certainty and disclosure level.

`praxis-4b` is this kind of fine-tune already: a LoRA on Qwen3.5-4B trained on engine evidence graphs with typed claims. It removes most contract violations (see results). A Scan64-specific fine-tune is therefore not needed to beat the baseline. It stays an option for the remaining `tactic_not_supported` and `evaluation_not_supported` rejections. The licence of the scraped GameKnot text was not checked here and must be cleared before any training.

## Method

- Benchmark: `benchmarks/narrator/`. One command from the repository root with Ollama serving:
  `uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml --setup`
- Positions: 50 fixed positions in `benchmarks/narrator/positions.json` (sha256 `a515d7fd2ff3…`), built deterministically by `benchmarks/narrator/build_positions.py` from the m48 recorded real-game corpora in `tests/fixtures/m48/` through the production diagnosis pipeline. Trusted codes only, at most 9 per code and 2 per game. Codes: `board_awareness.hanging_piece`, `tactics.fork.knight`, `threat_processing.allowed_mate`, `threat_processing.missed_capture`, `threat_processing.missed_check`, `threat_processing.missed_direct_threat`.
- Runs: each model 3 times per position (150 calls per model). Temperature and sampling come from each model's Modelfile in `benchmarks/narrator/modelfiles/`. Every model gets `num_ctx` 4096 and `num_predict` 1024.
- Pipeline under test: the production `scan64.explanations.assembly:resolve_explanation` with an `OllamaExplanationAdapter` built by `create_llm_provider`, at commit `c589f317` (assembly.py sha256 `255ce24b73f9`), before the #367 prompt changes. The entry point and grounding context are dotted paths in the config, so a rerun after #367 needs no code change.
- Request override: `think = false` is merged into every `/api/chat` body. The app's adapter sends no `think` field. In a smoke run every Qwen3/Qwen3.5 model spent its whole output budget thinking and returned no JSON.
- Metrics:
  - Grounding pass: share of calls where the pipeline accepted the model output instead of falling back to the template (#366 validator).
  - Provider errors: share of calls that returned no valid explanation JSON. Every provider error in the 5090 run is an answer that hit the 1024-token cap.
  - False chess statements per explanation: claims rejected for a wrong chess fact (illegal or unverified move or line, piece not on square, unsupported evaluation, mate, check, material, tactic, wrong side to move, unparsed chess token), per generated explanation. Each claim is validated on its own against the same grounding context.
  - Rejected claims per explanation: all rejections, including contract violations (unknown evidence id, certainty or disclosure above the evidence). The validator rejects a contract violation before it reads the text, so a model whose claims all cite unknown evidence scores 0 false chess statements.
  - SAN legal / correct: every SAN token in claim text. Legal: parses on a board of the cited evidence. Correct: the move, check and mate suffix match an evidence move.
  - Latency: wall time of the provider call. "Within 4 s" is the share of calls inside the production local-Ollama limit (`_OPERATION_SECONDS = 4.0` in `src/scan64/providers/llm/local_ollama.py`). The benchmark client allows 120 s so slow models are measured, not cut off.
  - VRAM: Ollama `size_vram` of the loaded model.

## Results

### Reference run: RTX 5090 (all six models)

`benchmarks/narrator/results/summary_20261126_162415.{md,json}`, runs in `runs_20261126_162415.jsonl`. NVIDIA GeForce RTX 5090 32 GB, Ollama 0.40.18, 50 positions x 3 runs.

| Model | Role | Params (B) | Grounding pass | Provider errors | Claims / expl. | False chess statements / expl. | Rejected claims / expl. | SAN legal | SAN correct (SAN tokens) | Latency p50 / p95 (s) | Within 4 s | VRAM (GiB) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chessgpt-base | chess | 2.8 | 0.0% | 55.3% | 3.79 | 0.00 | 3.79 | — | — (0) | 2.98 / 3.28 | 99.3% | 2.26 |
| praxis-4b | chess | 4.2 | 80.0% | 6.0% | 4.30 | 0.19 | 0.19 | 100.0% | 100.0% (102) | 1.39 / 3.70 | 98.0% | 2.76 |
| qwen3-4b-chess-explain | chess | 4.0 | 33.3% | 4.7% | 3.81 | 1.77 | 2.10 | 91.4% | 76.9% (221) | 0.75 / 3.42 | 100.0% | 2.96 |
| chesslm-qwen3-8b | chess | 8.2 | 22.0% | 0.0% | 6.41 | 0.65 | 3.15 | 97.9% | 97.9% (146) | 1.92 / 3.30 | 98.7% | 5.02 |
| qwen3.5-4b | baseline | 4.7 | 27.3% | 2.0% | 3.69 | 0.24 | 2.61 | 100.0% | 100.0% (68) | 1.56 / 4.02 | 94.7% | 2.92 |
| qwen3-8b | baseline | 8.2 | 1.3% | 67.3% | 8.53 | 0.00 | 8.02 | 100.0% | 100.0% (78) | 4.94 / 5.13 | 26.0% | 5.20 |

Rejection reasons per model (claim counts over 150 calls, from the summary JSON):

| Model | Main rejection reasons |
|---|---|
| chessgpt-base | `unknown_evidence` 254 |
| praxis-4b | `tactic_not_supported` 15, `evaluation_not_supported` 9, `mate_not_supported` 3 |
| qwen3-4b-chess-explain | `piece_not_on_square` 77, `unparsed_chess_token` 72, `tactic_not_supported` 51, `unknown_evidence` 27, `certainty_exceeded` 16, `move_not_verified` 16, `illegal_line_move` 13 |
| chesslm-qwen3-8b | `certainty_exceeded` 200, `unknown_evidence` 173, `unparsed_chess_token` 57, `mate_not_supported` 11 |
| qwen3.5-4b | `unknown_evidence` 157, `certainty_exceeded` 149, `disclosure_exceeded` 41, `malformed_line_move` 11, `unparsed_chess_token` 10 |
| qwen3-8b | `certainty_exceeded` 331, `unknown_evidence` 61 |

`chessgpt-base` and `qwen3-8b` score 0.00 false chess statements because the validator rejects almost all their claims on the contract (unknown evidence, certainty) before it reads the text. `qwen3-8b` also hits the 1024-token cap in 67.3% of calls.

### Consumer laptop GPU: RTX 4080 Laptop (four models)

`benchmarks/narrator/results/summary_20261126_145813.{md,json}`, NVIDIA GeForce RTX 4080 Laptop GPU 12 GB, Ollama 0.40.18, 50 positions x 3 runs, no other model loaded on the GPU during any call. Only latency is listed: the qwen3.5-4b and qwen3-8b lines of this runs file ran on the RTX 5090 (the file header still names the 4080) and are left out.

| Model | Latency p50 / p95 (s) | Within 4 s |
|---|---|---|
| chessgpt-base | 7.70 / 8.04 | 32.7% |
| praxis-4b | 3.27 / 6.38 | 73.3% |
| qwen3-4b-chess-explain | 1.37 / 8.77 | 76.7% |
| chesslm-qwen3-8b | 5.20 / 8.82 | 12.7% |

### CPU only

`benchmarks/narrator/results/cpu/summary_20261126_170614.{md,json}`, runs in `runs_20261126_170614.jsonl`, config `benchmarks/narrator/narrator_cpu.toml`. A separate Ollama 0.40.18 server on 127.0.0.1:11436 started with `CUDA_VISIBLE_DEVICES=-1 OLLAMA_LLM_LIBRARY=cpu`; its device discovery listed only `cpu`, and Ollama reported `size_vram` 0 for both models, so no layer ran on the GPU. CPU: Intel Core i9-14900HX, 32 logical cores. First 10 positions x 1 run, same pipeline and module hash as the GPU runs. The summary header names the GPU present in the machine, not one in use.

| Model | Latency p50 / p95 (s) | Within 4 s | Grounding pass (10 calls) |
|---|---|---|---|
| praxis-4b | 15.19 / 51.86 | 0.0% | 100.0% |
| qwen3.5-4b | 20.92 / 77.71 | 0.0% | 20.0% |

Neither model answers within the 4 s production limit on this CPU. With 10 calls, p95 is close to the slowest call.

## Defect found in the #366 validator

The full run crashed on a validator defect. `src/scan64/explanations/validator.py` `_tactic` looks up the matched tactic with `next(...)` over the `_TACTIC_WORDS` keys, which spell "back rank" with a space. The tactic regex also accepts "back-rank", so claim text such as "This is a back-rank weakness." raised `StopIteration`, which escaped `resolve_explanation` as `RuntimeError`. In production, an LLM claim saying "back-rank" crashed the caller instead of falling back to the template. The runner records such calls as outcome `pipeline_exception` and such claims as `validator_exception`. In the 5090 run this hit 1 call of `qwen3-4b-chess-explain` (5 claims) and 1 call of `qwen3-8b` (1 claim).

Fixed on `epic/371` in commit `dddcd85` ("fix(explanations): accept hyphenated back-rank in claim text tactic check"). The benchmark ran on the pinned pre-fix module.

## Configuration

The narrator model is chosen by configuration with no code change, through either of two existing paths:

1. Owner setting (desktop, local Ollama). `PUT /v1/players/{player_id}/explanation-settings` (Settings screen) stores `model` in `OwnerExplanationSettings`. The model must be installed in the local Ollama at the fixed `http://127.0.0.1:11434` (`LOCAL_OLLAMA_BASE_URL`, `src/scan64/providers/llm/local_ollama.py`) and pass the name check `[A-Za-z0-9][A-Za-z0-9._:/-]*`. Calls run under the fixed 4 s limit.
2. Operator file. `SCAN64_LLM_CONFIG` names an absolute path to a TOML file read by `LLMProviderConfig.from_toml` (`src/scan64/providers/llm/config.py`). It takes precedence over the owner setting.

For the recommended model, create the Ollama model from the benchmark Modelfile (operator setup, not app code): substitute `@GGUF@`, `@NUM_CTX@` and `@NUM_PREDICT@` in `benchmarks/narrator/modelfiles/praxis-4b.Modelfile` (as `--setup` does) and run `ollama create scan64-bench-praxis-4b -f <rendered Modelfile>` against the 11434 server. Then select it:

```toml
# file named by SCAN64_LLM_CONFIG
[llm]
provider = "ollama"
model = "scan64-bench-praxis-4b"
base_url = "http://127.0.0.1:11434"
```

or, in Settings, pick the installed model `scan64-bench-praxis-4b:latest` (owner setting `model = "scan64-bench-praxis-4b:latest"`; the setting must equal a name listed by `/api/tags`).

Difference from the benchmark: the benchmark merged `think = false` into each request. The app adapter sends no `think` field and this cannot be set by configuration. `praxis-4b` does not depend on it: its Modelfile template closes the thinking block before the answer. The two Qwen3/Qwen3.5 baselines do depend on it; through the app they would think first (smoke-run observation in the method section). The benchmark also capped output at `num_predict` 1024 in the Modelfile; the rendered Modelfile above carries the same cap. No app default was changed.

## Recommendation

Use `praxis-4b` (`praxis-chess/praxis-chess-reasoner-qwen3.5-4b-GGUF`, Q4_K_M, Apache-2.0) as the narrator when an owner or operator enables an LLM. Against its same-size baseline `qwen3.5-4b` (same Qwen3.5-4B base weights), on the RTX 5090, 150 calls each:

| Metric | praxis-4b | qwen3.5-4b |
|---|---|---|
| Grounding pass | 80.0% | 27.3% |
| False chess statements / expl. | 0.19 | 0.24 |
| Rejected claims / expl. | 0.19 | 2.61 |
| SAN legal / correct | 100.0% / 100.0% (102 tokens) | 100.0% / 100.0% (68 tokens) |
| Provider errors (output cap) | 6.0% | 2.0% |
| Latency p50 / p95, RTX 5090 | 1.39 / 3.70 s | 1.56 / 4.02 s |
| Within 4 s, RTX 5090 | 98.0% | 94.7% |
| Latency p50 / p95, CPU (10 calls) | 15.19 / 51.86 s | 20.92 / 77.71 s |
| VRAM | 2.76 GiB | 2.92 GiB |

The gain is mostly contract compliance: `qwen3.5-4b` loses claims to `unknown_evidence` (157) and `certainty_exceeded` (149); `praxis-4b` has neither. Its remaining rejections are wrong chess facts in text (`tactic_not_supported` 15, `evaluation_not_supported` 9, `mate_not_supported` 3), and the validator rejects them, so the template floor still holds.

Limits of the measurement: on the RTX 4080 Laptop `praxis-4b` answers within 4 s in 73.3% of calls (p95 6.38 s), so on that class of GPU about a quarter of calls fall back to the template on the time limit. On CPU no call met the 4 s limit. The prompt builder is the pre-#367 one; rerun with `uv run python -m benchmarks.narrator.run_benchmark benchmarks/narrator/narrator.toml` after #367 lands. This is a narrator wording measurement, not a measurement of chess improvement for players.

## Decision

2026-11-26: the project chose `praxis-4b` as the narrator model. Choosing the narrator is an ongoing process, not a single decision. Rerun this benchmark whenever the prompt builder, the validator or a candidate model changes, and add new candidates to `benchmarks/narrator/narrator.toml`. A model replaces `praxis-4b` only when it beats it on grounding pass rate and false chess statements in the same run.
