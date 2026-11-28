# Narrator fine-tune on verified annotations (#465)

Result (2026-11-28): **not usable as the narrator.** The #464 data gives 3 training examples, so no model can learn the language from it. The tuned Qwen3-1.7B is 91% faithful and 14% faithful and lively. The bar is 95% and 80%. The default model does not change, and nothing is packaged.

## Data

`scripts/build-training-pairs.py` at commit 8c29ddd, run on linux-5090 (Stockfish 18), keeps **3 pairs of 1,112 sentences**. The same build on windows-4080 keeps 1 (PR #537). The pair code is the same, so the difference is probably the 4-thread engine search (not tested). Both builds put 0 pairs in the held-out split.

A pair maps all facts of a move to one sentence. The narrator words one merged fact per call, so `data.py` takes the merged fact with the fewest failed checks. The app's own sentence check (#454) rejects all 3 sentences:

| Human sentence | Failed check |
|---|---|
| This comes to the same as 3.Nf3. | states the alternative move as played |
| 14...Qc7 leads to the same conclusion. | states the alternative move as played |
| If 29.Re1, 29...Qc5. | move Qc5 |

Two of them state the alternative move as played, and one names a move that its fact does not hold. A tuned model is trained on sentences the app would not show. All 3 come from *Chess Strategy* (1915, public domain in the US, eBook #5614). Chess Fundamentals is excluded in `finetune.toml`: its 1934 revision is an open copyright question.

No LLM-labelled pairs were substituted: #465 asks for human annotations.

## Training

| Item | Value |
|---|---|
| Base | Qwen/Qwen3-1.7B, revision 70d244cc, Apache-2.0 |
| Recipe | chess-annotator (MIT): LoRA rank 32, alpha 64, dropout 0.05, learning rate 1e-4 cosine, batch 16, 2 epochs |
| Optimizer steps | 2 |
| Seed | 465 |
| Loss on the 3 examples | 7.99 before, 3.54 after |
| Peak GPU memory | 17.2 GiB |

`results/data.json` and `results/train.json` hold the data, config, base, adapter and merged-weight SHA-256. The weights stay outside the repository. A second run with the same data and seed, on an otherwise equal config, gives a different adapter SHA-256 and a loss after of 3.54 against 3.58. The run repeats in method, not bit for bit.

## Evaluation

Method:
- The #454 pipeline on three sets: every comment position of the 2 held-out games, the legal #462 game moves (H2 is illegal, #500), and the Analysis Board.
- One fresh blind judge read all 240 comments, shuffled, with no labels (`results/judge-brief.md`).
- Phrase bank (#461): deterministic, one comment per position, game moves only. The Analysis Board shows the template without a model.

| Set | Arm | n | Faithful (±95%) | Faithful and lively | Lively | Clear | Fallback sentences | p50 s | p95 s | GPU GiB |
|---|---|---|---|---|---|---|---|---|---|---|
| Held-out games | base | 29 | 83% ±14 | 14% | 1.17 | 2.83 | 18.9% | 1.85 | 2.45 | 3.22 |
| Held-out games | tuned | 29 | 90% ±11 | 0% | 1.00 | 2.79 | 15.6% | 1.39 | 2.38 | 3.22 |
| Held-out games | default | 29 | 72% ±16 | 69% | 2.52 | 2.86 | 1.1% | 0.62 | 0.99 | 2.84 |
| Held-out games | phrase-bank | 29 | 93% ±9 | 90% | 1.97 | 3.00 | 0.0% | - | - | 0 |
| #462 game moves | base | 20 | 60% ±21 | 30% | 1.40 | 2.65 | 15.4% | 1.62 | 3.12 | 3.22 |
| #462 game moves | tuned | 20 | 95% ±10 | 50% | 1.50 | 2.70 | 13.8% | 1.42 | 2.54 | 3.22 |
| #462 game moves | default | 20 | 25% ±19 | 25% | 2.50 | 2.90 | 6.2% | 0.71 | 1.28 | 2.84 |
| #462 game moves | phrase-bank | 4 | 75% ±42 | 50% | 1.50 | 3.00 | 7.7% | - | - | 0 |
| Analysis Board | base | 20 | 55% ±22 | 50% | 1.65 | 2.80 | 0.9% | 2.36 | 3.11 | 3.22 |
| Analysis Board | tuned | 20 | 90% ±13 | 0% | 1.05 | 2.35 | 5.5% | 1.86 | 2.68 | 3.22 |
| Analysis Board | default | 20 | 100% ±0 | 100% | 2.85 | 3.00 | 0.0% | 1.04 | 1.18 | 2.84 |
| All | base | 69 | 68% ±11 | 29% | 1.38 | 2.77 | 10.6% | 2.08 | 3.11 | 3.22 |
| All | tuned | 69 | 91% ±7 | 14% | 1.16 | 2.64 | 10.9% | 1.44 | 2.68 | 3.22 |
| All | default | 69 | 67% ±11 | 65% | 2.61 | 2.91 | 1.9% | 0.80 | 1.18 | 2.84 |
| All | phrase-bank | 33 | 91% ±10 | 85% | 1.91 | 3.00 | 1.0% | - | - | 0 |

Limits:
- Held-out: 29 comments on 22 distinct moves. Seven moves carry two comments and count twice.
- No interval meets the ±5 of #462. The arms are far from the bar, so more samples do not change the outcome.
- Base and tuned ran unquantized (bf16) on a Transformers server, with Qwen's sampling (temperature 0.7). Their GPU memory and seconds are not a GGUF's. The default ran on Ollama at temperature 1, with other sessions on the GPU. No footprint claim is made for the tuned model.
- The GGUF conversion was not run: the session's permission system refused the llama.cpp converter, and Ollama has no MLX runtime to import safetensors here.
- The default scores 67% here and 96% in #462. Facts, positions and judge differ. 21 of its 23 failures are a "!" or praise in the sentence about the played move, which the rubric counts as false. Only its board set (100%) is free of them.

Findings:
- The tuned model is more faithful than its base (91% against 68%) and flatter: lively 1.16 against 1.38. Two optimizer steps cannot teach chess language.
- The phrase bank is 91% faithful and 85% faithful and lively, with no model.
- The pipeline's fallback rate stays at about 11% for the small model.

## Next step

Recommended: raise the number of verified pairs before training again. The recipe used 13,001 pairs. At 3 pairs per 1,112 sentences, the current filter would need about 4.8 million sentences.

1. #464: 360 sentences drop because the move is not in the facts. Analyse each move the annotator names, not only the game move and the engine lines.
2. #464: apply the app's sentence check per merged fact, so every kept pair has a target the app would show.
3. #463: a source in algebraic notation (the three pending books are scans).
4. If that is not enough, the owner decides whether LLM-labelled, code-checked pairs (the chess-annotator method) are acceptable. That changes the intent of #465.

## Rebuild

```sh
uv run python -m benchmarks.narrator.finetune.data --data-dir <data> --out <dir>/train.jsonl
<venv>/bin/python benchmarks/narrator/finetune/train.py --data <dir>/train.jsonl --base <base> --out <dir>/run --report <dir>/train.json
uv run python -m benchmarks.narrator.finetune.evaluate positions --data-dir <data> --engine stockfish
<venv>/bin/python benchmarks/narrator/finetune/serve.py --model <dir>/run/merged --name scan64-ft-tuned --port 11499
uv run python -m benchmarks.narrator.finetune.evaluate run --arm tuned --model scan64-ft-tuned --url http://127.0.0.1:11499 --out game-tuned.jsonl
uv run python -m benchmarks.narrator.finetune.evaluate summarise <game records> --board <arm>=<board records> --key results/key.json --judgements results/items-judgements.json
```
