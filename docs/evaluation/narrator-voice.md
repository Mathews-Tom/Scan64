# Narrator voice benchmark (#457, #458, #462)

Question: how can a small local model word chess facts in a lively, human voice without saying anything false?

Answer: code writes each fact, the model rewrites one fact per call as one styled sentence, and code checks every sentence before a player sees it. With this method, gemma4-e4b Q4_K_S (2.84 GiB of GPU memory) is 96% faithful and 96% faithful and lively on held-out positions, judged blind. With whole-comment prompts, gemma4-e4b Q3_K_M reached 78–94% faithful and at most 80% faithful and lively (runs 4–5).

Everything on this page is rebuilt from the stored records by `python -m benchmarks.narrator.voice summarise`. The code is in `benchmarks/narrator/voice/`, and the records are in `benchmarks/narrator/voice/results/`.

## Method

The narrator pipeline of runs 6–9:

1. **Code renders the position in plain words.** The application reads the FEN. The model sees "White: king on g1; rook on d1; ..." and never a FEN or a PGN.
2. **Code writes the facts.** Each fact has a role: "The move", "What it misses", "What follows" or "What White could have played". Judgements are facts too, for example "7.Be2 was not the only way to block the check". python-chess checks every fact (`verify.py`).
3. **The model rewrites one fact per call.** The prompt holds the style name, a short card on how the style writes, one example sentence for each example fact, and the fact to rewrite. The model never sees the other facts, so it cannot move a piece or a role from one fact to another.
4. **Code checks the sentence.** The sentence fails when it:
   - names a move, a square, a piece or a chess term that its fact does not contain;
   - keeps none of the fact's moves or squares;
   - is longer than one sentence.

   Code also removes a "!" after the played move.
5. **Code retries, then falls back.** A failed sentence is asked for again, up to 3 calls in all. After 3 failures, the plain fact is used.
6. **Code stops repetition (run 8 on).** A colour word is a word of a sentence that its fact does not contain. When a sentence reuses a colour word of an earlier sentence, it is asked for once more with "Do not use: <words>".
7. **Code joins the sentences.**

Every call uses a local Ollama with thinking off and a context of 4096 tokens.

## What each run changed

| Run | Structure | Result (blind judge unless marked) |
|---|---|---|
| 1 | Whole comment. Four ways to give a style: name only, traits, traits and examples, examples only | Word check (code): name only 99% and flat; traits and examples 83% |
| 2 | Run 1 plus the position in plain words | Word check: traits and examples 83% → 92% |
| 3 | Run 2 plus a role for each fact | Faithful and lively 20% → 40% (traits and examples) |
| 4 | Judgements written as facts; a rule against unsupported judgement | Tal 78%, Chernev 90% faithful |
| 5 | "White could have played X instead of Y"; the square after each move; "!" repair | Tal 86%, Chernev 94%; gemma4-12b 96% |
| 6 | One fact per call, checked, retried, else the plain fact | Tal 96% faithful and lively |
| 7 | Run 6 plus the comment so far as context | 90%; 31 of 100 comments repeat themselves. Reverted |
| 8 | Run 6 plus the code check against repeated colour words | 91% (n = 100); no repeats |
| 9 | Run 8 on 5 held-out positions, two models, sampling settings and an "own words" rule | gemma4-e4b Q4_K_S 96%; praxis-4b 50–62% when it samples |

Runs 1–8 use positions P1–P5. These positions were also used to tune the prompts. Run 9 uses positions H1–H5, which have themes and wording not used before: a missed mate, a skewer, a stalemate, a pin and an allowed fork.

## Blind-judge protocol

A fresh model session judges the comments. It reads only an items file. Each item has:
- an id;
- the position in plain words;
- the facts with their roles;
- the comment.

The items are shuffled. They carry no run, model, style or cell label. The key that unblinds them stays in another file, which the judge must not open.

The judge gives each comment:

| Field | Meaning |
|---|---|
| `faithful` | True only if every statement is true for the facts and the position. When in doubt, false. |
| `error` | When not faithful: the false words and why, in 15 words or fewer. |
| `lively` | 1 = a flat restatement, 2 = some colour or a clear story, 3 = vivid, as Chernev or Tal write. |
| `clear` | 1–3: can a club player follow what happened and why? |
| `jargon` | True for chess jargon or abstract terms a club player would not use, or evaluation numbers. |

"Faithful and lively" counts the comments that are faithful and score 2 or more on `lively`.

A comparison uses **one judge for all its arms**. Two judges can differ by up to 28 points on the same model (run 8, 12 models). When a comparison is too large for one judge, split it into two halves. Each arm must have the same number of comments in both halves (`blind --halves`), and each half gets its own judge.

The judge brief of run 9, word for word (only the folder path is removed):

```text
You are a blind judge for a chess-writing experiment. Read only blind9-items.json: 250 items. Each item has an id, the position in plain words, a list of verified facts (with role labels such as "The move:", "What it misses:", "What White could have played:"), and a short comment written for a club player. Judge every comment only against its own position and facts. Do not open any other file in that folder; the key and run files would unblind you.

For each comment give:
1. "faithful": true if every statement is true given the facts and the position, else false. False includes:
   - a fact given the wrong role (e.g. calling an advantage "the price");
   - a wrong side, piece, square or move;
   - a chess claim the facts do not support (advantage, danger, decisive, material, development, a mate not in the facts, calling a move forced or necessary when the facts say otherwise);
   - an attacked piece described as taken or captured, or a capture placed before or at the same time as the check that must come first;
   - "!" after a move the facts present as a mistake;
   - nonsense.
   Rhetorical colour that restates a fact is allowed ("the knight goes hunting" for a knight that attacks a rook). Judge the chess, not the grammar. When in doubt, false.
2. "error": when faithful is false, quote the false words and say why in at most 15 words; else "".
3. "lively": 1 = a flat restatement of the facts, 2 = some colour or a clear story, 3 = vivid, the way a lively annotator such as Chernev or Tal writes.
4. "clear": 1–3. Can a club player follow what happened and why? 3 = yes, at once.
5. "jargon": true if it uses chess jargon or abstract terms a club player would not use naturally ("positional", "prophylaxis", "tempo", "structure", evaluation numbers).

Judge each item on its own reading; no keyword rules. You may use a small script only for bookkeeping. Write blind9-judgements.json as a JSON list of {"id","faithful","error","lively","clear","jargon"}, one entry per item, all 250. Report the count judged and the share faithful.
```

Each stored blind set has its own brief next to it (`results/<run>/judge-brief-<set>.md`). The briefs of runs 2–8 went, one after the other, to the same judge session.

## Rerun a run

Start a local Ollama with the model, then run from the repository root. A run never overwrites a stored file. The `scan64-bench-*` models are the ones the [narrator model benchmark](narrator-models.md) creates from `benchmarks/narrator/modelfiles/`.

| Stored file | Command |
|---|---|
| `run1/run1.jsonl` | `python -m benchmarks.narrator.voice run 1 --out run1.jsonl` |
| `run2/run2-board.jsonl` | `python -m benchmarks.narrator.voice run 2 --out run2-board.jsonl` |
| `run3/run3-roles.jsonl` | `python -m benchmarks.narrator.voice run 3 --out run3-roles.jsonl` |
| `run4/run4.jsonl` | `python -m benchmarks.narrator.voice run 4 --out run4.jsonl` |
| `run5/run5.jsonl` | `python -m benchmarks.narrator.voice run 5 --out run5.jsonl` |
| `run5/run5-12b.jsonl` | `python -m benchmarks.narrator.voice run 5 --model scan64-bench-gemma4-12b:latest --out run5-12b.jsonl` |
| `run6/run6.jsonl` | `python -m benchmarks.narrator.voice run 6 --out run6.jsonl` |
| `run7/run7-tal.jsonl` | `python -m benchmarks.narrator.voice run 7 --out run7-tal.jsonl` |
| `run8/run8-tal.jsonl` | `python -m benchmarks.narrator.voice run 8 --out run8-tal.jsonl` |
| `run8-models/run8-<model>.jsonl` | `python -m benchmarks.narrator.voice run 8 --samples 10 --model <Ollama name> --out run8-<model>.jsonl` |
| `run9/run9-A-gemma.jsonl` | `python -m benchmarks.narrator.voice run 9 --model scan64-bench-gemma4-e4b-q4ks:latest --options temperature=1,top_p=0.95,top_k=64 --out run9-A-gemma.jsonl` |
| `run9/run9-B-gemma-own.jsonl` | the cell A command plus `--own` |
| `run9/run9-C-praxis-t0.jsonl` | `python -m benchmarks.narrator.voice run 9 --model scan64-bench-praxis-4b:latest --options temperature=0 --out run9-C-praxis-t0.jsonl` |
| `run9/run9-D-praxis-t1.jsonl` | the cell A command with `--model scan64-bench-praxis-4b:latest` |
| `run9/run9-E-praxis-t1-own.jsonl` | the cell D command plus `--own` |

The Ollama names of the run 8 models are in `results/run8-models/models-vram.txt`. Without `--url`, the client uses `http://127.0.0.1:11434`.

A new record carries `meta`: the git commit, whether the tree had uncommitted changes, the model name and its Ollama digest, the Ollama version, the sampling options and the prompt version.

Build a blind set for new records, and rebuild a stored one:

```sh
python -m benchmarks.narrator.voice blind a.jsonl b.jsonl --facts heldout --seed 1 --prefix x --out blind/
python -m benchmarks.narrator.voice blind a.jsonl b.jsonl --facts heldout --seed 1 --prefix x --halves --out blind/
python -m benchmarks.narrator.voice blind --stored run9 --out rebuilt/
```

## Results

The tables below are the output of `summarise`. A test checks that this section matches that output.

<!-- summarise:start -->

### Runs 1-3: word check (code), share of comments that pass

| Run | plain (n) | name (n) | traits (n) | traits+examples (n) | examples (n) |
|---|---|---|---|---|---|
| 1 | 100% (15) | 99% (90) | 87% (90) | 83% (90) | 97% (90) |
| 2 | 100% (15) | 100% (90) | - | 92% (90) | 96% (90) |
| 3 | 100% (15) | 100% (90) | - | 86% (90) | 92% (90) |

### Runs 2-3: blind judge by style, traits + examples

| Style | n | Faithful | Lively (1-3) |
|---|---|---|---|
| Capablanca | 10 | 90% | 1.00 |
| Chernev | 10 | 80% | 1.30 |
| Tal | 10 | 70% | 2.80 |
| Lasker | 10 | 50% | 1.40 |
| Nimzowitsch | 10 | 40% | 2.60 |
| Bronstein | 10 | 30% | 1.80 |

### Run 2 against run 3: facts labelled by role

| Run | n | Faithful | Faithful and lively |
|---|---|---|---|
| 2 (no roles) | 30 | 57% | 20% |
| 3 (roles) | 30 | 63% | 40% |

### Runs 4-8: blind judge by style

| Run | Model | Style | n | Faithful | Faithful and lively | Lively | Clear |
|---|---|---|---|---|---|---|---|
| 4 | gemma4-e4b Q3_K_M | Capablanca | 50 | 92% | 0% | 1.00 | 2.64 |
| 4 | gemma4-e4b Q3_K_M | Chernev | 50 | 90% | 66% | 1.76 | 2.88 |
| 4 | gemma4-e4b Q3_K_M | Tal | 50 | 78% | 78% | 2.86 | 2.34 |
| 5 | gemma4-e4b Q3_K_M | Chernev | 50 | 94% | 38% | 1.44 | 2.88 |
| 5 | gemma4-e4b Q3_K_M | Capablanca | 50 | 90% | 0% | 1.00 | 2.68 |
| 5 | gemma4-e4b Q3_K_M | Tal | 50 | 86% | 80% | 2.46 | 2.60 |
| 5 (12B) | gemma4-12b | Chernev | 50 | 100% | 76% | 1.78 | 3.00 |
| 5 (12B) | gemma4-12b | Capablanca | 50 | 100% | 0% | 1.00 | 2.68 |
| 5 (12B) | gemma4-12b | Tal | 50 | 88% | 88% | 2.92 | 2.50 |
| 6 | gemma4-e4b Q3_K_M | Chernev | 50 | 100% | 0% | 1.00 | 3.00 |
| 6 | gemma4-e4b Q3_K_M | Tal | 50 | 96% | 96% | 2.86 | 2.88 |
| 7 | gemma4-e4b Q3_K_M | Tal | 100 | 90% | 90% | 2.73 | 2.64 |
| 8 | gemma4-e4b Q3_K_M | Tal | 100 | 91% | 91% | 2.85 | 2.86 |

### Runs 7-8: code metrics

| Run | Comments | Coverage | Fallback sentences | Repeating | p50 s | p95 s |
|---|---|---|---|---|---|---|
| 7 | 100 | 98.5% | 4.7% | 31 | 0.54 | 1.34 |
| 8 | 100 | 99.1% | 5.6% | 0 | 0.50 | 0.74 |

### Run 8 pipeline, 12 models: blind judges (#462)

| Model | GPU GiB | Faithful (±95%) | Judge 1 / Judge 2 | Faithful and lively | Lively | Clear | Fallback sentences | p95 s |
|---|---|---|---|---|---|---|---|---|
| gemma3 270M | 0.30 | 72% ±12 | 80% / 64% | 0% | 1.00 | 2.48 | 70.0% | 7.02 |
| gemma3 1B | 0.82 | 12% ±9 | 24% / 0% | 12% | 2.00 | 1.22 | 8.8% | 0.36 |
| praxis-2b (chess LoRA) | 1.30 | 38% ±13 | 48% / 28% | 12% | 1.50 | 2.20 | 35.3% | 0.60 |
| second-2b (chess SFT) | 1.30 | 20% ±11 | 16% / 24% | 16% | 2.00 | 1.96 | 12.4% | 1.08 |
| llama3.2 1B | 1.41 | 30% ±13 | 36% / 24% | 4% | 1.24 | 1.56 | 21.8% | 69.49 |
| qwen3 1.7B | 1.59 | 34% ±13 | 24% / 44% | 22% | 1.56 | 2.30 | 2.9% | 0.31 |
| qwen3.5 2B | 2.20 | 12% ±9 | 12% / 12% | 10% | 2.54 | 1.58 | 10.0% | 1.16 |
| gemma4-e4b Q3_K_M (current default) | 2.43 | 86% ±10 | 100% / 72% | 86% | 2.14 | 2.86 | 5.6% | 0.74 |
| praxis-4b (chess LoRA) | 2.76 | 100% | 100% / 100% | 68% | 1.88 | 3.00 | 5.9% | 0.96 |
| gemma4-e4b Q4_K_S | 2.84 | 98% ±4 | 100% / 96% | 98% | 2.08 | 2.96 | 2.4% | 0.69 |
| qwen3.5 4B | 2.92 | 58% ±14 | 68% / 48% | 58% | 2.70 | 2.40 | 1.8% | 1.64 |
| caissa-m1-8b (reference, above budget) | 4.43 | 20% ±11 | 20% / 20% | 20% | 2.64 | 2.04 | 16.5% | 1.06 |

### Run 8 pipeline, 12 models: coverage

| Model | Comments | Coverage |
|---|---|---|
| gemma3 270M | 50 | 98.5% |
| gemma3 1B | 50 | 72.4% |
| praxis-2b (chess LoRA) | 50 | 97.1% |
| second-2b (chess SFT) | 50 | 88.5% |
| llama3.2 1B | 50 | 87.1% |
| qwen3 1.7B | 50 | 89.1% |
| qwen3.5 2B | 50 | 79.4% |
| gemma4-e4b Q3_K_M (current default) | 100 | 99.1% |
| praxis-4b (chess LoRA) | 50 | 100.0% |
| gemma4-e4b Q4_K_S | 50 | 98.8% |
| qwen3.5 4B | 50 | 91.8% |
| caissa-m1-8b (reference, above budget) | 50 | 82.6% |

### Run 9, held-out positions (#462)

| Cell | Model | Sampling | "Own words" rule | Faithful (±95%) | Faithful and lively | Lively | Fallback sentences | p95 s |
|---|---|---|---|---|---|---|---|---|
| A | gemma4-e4b Q4_K_S | T 1, top_p 0.95, top_k 64 | no | 96% ±5 | 96% | 2.04 | 0.0% | 0.56 |
| B | gemma4-e4b Q4_K_S | T 1, top_p 0.95, top_k 64 | yes | 98% ±4 | 98% | 2.08 | 0.0% | 0.55 |
| C | praxis-4b | T 0 (its model file) | no | 100% | 0% | 1.00 | 12.5% | 0.60 |
| D | praxis-4b | T 1, top_p 0.95, top_k 64 | no | 62% ±13 | 58% | 1.96 | 1.9% | 0.92 |
| E | praxis-4b | T 1, top_p 0.95, top_k 64 | yes | 50% ±14 | 50% | 2.02 | 0.0% | 0.72 |

### Run 9, faithful per held-out position

| Cell | Missed mate | Skewer | Stalemate | Pin | Allowed fork |
|---|---|---|---|---|---|
| A | 10/10 | 10/10 | 9/10 | 10/10 | 9/10 |
| B | 9/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| C | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| D | 7/10 | 7/10 | 8/10 | 3/10 | 6/10 |
| E | 8/10 | 5/10 | 7/10 | 1/10 | 4/10 |

<!-- summarise:end -->

## Limits

- **P1–P5 tuned the prompts.** Only the run 9 positions are held out, and there are 5 of them.
- **One judge per set, and it is a model.** The judges of runs 2–8 were one session, so its later sets were judged with the rule decisions it made on the earlier ones.
- **The p95 column of run 9 differs from #462 for two cells.** This page uses the nearest rank (the value at index int(0.95 × n)), as the 12-model table did. The #462 comment used the value one place lower. Cell C reads 0.60 s here and 0.59 s on #462. Cell D reads 0.92 s here and 0.84 s on #462.
- **The records of runs 1–8 do not name their model.** `summarise.py` names it from #457. Run 9 records name the model and the options; new records carry the full `meta`.
- **GPU memory here is not measured as in [narrator-models.md](narrator-models.md).** This page uses Ollama's `size_vram` (from `/api/ps`) after each run, for example 2.43 GiB for Q3_K_M. That page measures the GPU's used memory before and after the model loads, and gives 2.96–3.13 GiB for the same model.
- **One stored example breaks the sentence check.** The run 7 Chernev example "Notice that the e-file lies wide open, waiting for a rook!" names a rook that its fact does not name. Run 7 ran Tal only, so the example never reached a stored prompt.
