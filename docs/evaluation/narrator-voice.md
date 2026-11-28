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

A fresh model session judges the comments. It gets a folder that holds only the items file. Each item has:
- an id;
- the position in plain words;
- the facts with their roles;
- the comment.

The items are shuffled. They carry no run, model, style or cell label. The key that unblinds them is written to a separate path outside that folder (`blind --key-out`), so the judge cannot open it.

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

Start a local Ollama with the model, then run from the repository root. `run` refuses any `--out` under `results/`, even with `--force`, so it cannot overwrite a stored measurement. `run` checks every input and asks the server for the model before it opens `--out`, and opens it only once the first record exists, so a run that fails at start leaves no file behind. There is no resume: a run that stops later starts again with a new file. The `scan64-bench-*` models are the ones the [narrator model benchmark](narrator-models.md) creates from `benchmarks/narrator/modelfiles/`.

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

A rerun repeats the method, not the text. There is no seed option, so the model samples new comments. Compare a rerun with a stored run by its shares and their 95% intervals, never comment by comment.

A new record carries `meta`:

| Field | What it records |
|---|---|
| `commit` | The git commit the run ran on |
| `dirty`, `dirty_sha256` | Whether the tree had other changes than the `--out` file, and a SHA-256 of `git diff HEAD` plus the untracked files under `benchmarks/narrator/voice` |
| `model`, `model_digest` | The Ollama model name and its digest |
| `ollama_version` | The Ollama server version |
| `think`, `options` | Thinking (always off) and the sampling options, with `num_ctx` |
| `prompt_version`, `prompt_sha256` | The run's prompt version and a SHA-256 of all its prompt data: system prompt, style cards, examples, facts and FENs |
| `config` | The run, samples, styles, positions, "own words" rule, options and server URL |

Build a blind set for new records, and rebuild a stored one. `--out` is the folder for the judge, and `--key-out` must lie outside it. The key fields are the ones each record has (`run`, `test`, `variant`, `style`, `model`, `cell`, `own`, `n`), so stored run 9 records work too:

```sh
python -m benchmarks.narrator.voice blind a.jsonl b.jsonl --facts heldout --seed 1 --prefix x --out judge/ --key-out keys/blind-key.json
python -m benchmarks.narrator.voice blind a.jsonl b.jsonl --facts heldout --seed 1 --prefix x --halves --out judge/ --key-out keys/blind-key.json
python -m benchmarks.narrator.voice blind --stored run9 --out judge/ --key-out keys/blind9-key.json
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
| gemma4-e4b Q3_K_M | 2.43 | 86% ±10 | 100% / 72% | 86% | 2.14 | 2.86 | 5.6% | 0.74 |
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
| gemma4-e4b Q3_K_M | 100 | 99.1% |
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

### Stored files

| File | Kind | Entries | SHA-256 |
|---|---|---|---|
| run1/run1.jsonl | records | 375 | `854b42bbe670a992d7c929fc2730e2fec14bc6e8612b4b0dc461b33c3b68fa43` |
| run2/run2-board.jsonl | records | 285 | `909cf208858bc5a2f9a3ff45b2abe69bf4e0fef68860f103e78d4b5ed176809f` |
| run2-3-blind/blind-items.json | items | 190 | `cfbf360d144c93ba7076f2bb6f0f07fbc8294b50f478b02daebcfa77f43b8a47` |
| run2-3-blind/blind-judgements.json | judgements | 190 | `f5a627a133ef0e35d9237423f5dd1b2545dbe2e464660ef222fc4400d930475b` |
| run2-3-blind/blind-key.json | key | 190 | `a87157ac0a6ec4dfce4e25d98757d2fbd30f7a024b69819992dea50d84e06141` |
| run2-3-blind/judge-brief-blind.md | judge brief | - | `66e5044c3e8bb5c455eed522a4474dbaa8cb948515c852bc250b252cb261ae82` |
| run3/run3-roles.jsonl | records | 285 | `db2fbf15e35e4bbcefd0032e7bdde89db3a4f64278cfd789c5ef3c573c0c1731` |
| run4/blind4-items.json | items | 150 | `aee1dd27dc23a2f928cc85d5507e6850de1e27ce267650fc54c0b0c835922a60` |
| run4/blind4-judgements.json | judgements | 150 | `f89c0b69a959eda6210369b161c7b61a6cecc53d93679e46ceff8b8e1948a2fe` |
| run4/blind4-key.json | key | 150 | `6b56c0600c7b55bcbf85f3efcd6bf2318dcabf38641fc6bd517f86922460bff3` |
| run4/judge-brief-blind4.md | judge brief | - | `8c597219d568fb842a0f374470ce4308c8ac6e9169e9417a7da2628fe2c0ac7d` |
| run4/run4.jsonl | records | 150 | `469c74552a357bbbf60c75a923b2809c2589dc6bd7e1df0379a5fe4d7a9fe0bf` |
| run5/blind5-items.json | items | 150 | `e7aea87140dcda5edcd84af92b69782d960ae57e49546cf93399db6cf4f059ed` |
| run5/blind5-judgements.json | judgements | 150 | `ca6fa73ac6dfa22a85b175ed0fcbeece288194c05a69ae643579e54846f5ba02` |
| run5/blind5-key.json | key | 150 | `11f341bd07df6cee12258637824fd27792f47a82b9b986a55a3ab10fbe1f0995` |
| run5/blind5b-items.json | items | 150 | `12ff47e1332b616f2a34e7e2ae5714f042a1e359ed89b3daa6fe46b18993abe5` |
| run5/blind5b-judgements.json | judgements | 150 | `c1bd5245a26ba06290fe7c201d7a8a4561776638526136397e99bfac5f88f3d3` |
| run5/blind5b-key.json | key | 150 | `62ea1660b9f557d2a6307c40b2d1f89b67e1ddee7b98555ad69aac59ab771468` |
| run5/judge-brief-blind5.md | judge brief | - | `18e5b4792ed0f584c46c818f45a59751ae338fb157a4695441652886617a6dc0` |
| run5/judge-brief-blind5b.md | judge brief | - | `57e309f6dbd8cf61f010e4dd70ed22946e592c8e30af2736a89cb3e06b0cdd23` |
| run5/run5-12b.jsonl | records | 150 | `8ed67d690885a46549e86f6ff98f09f34ffa5212460d69580e31f17c3bc1b448` |
| run5/run5.jsonl | records | 150 | `c00fe3192558fcd81508abecbe8a0e263dd7b63d1153c49877da585d2d3b8588` |
| run6/blind6-items.json | items | 100 | `28e03d6a150027a063a48e39232aed4bb2cb16b2bc018974a4f14121bae2ed0f` |
| run6/blind6-judgements.json | judgements | 100 | `122fad829c1242690d6ae6c390bfa49da0c9d78efc6dde9f111a6ac4a91365ad` |
| run6/blind6-key.json | key | 100 | `1ab7fba1c781aab7e244af3f9f39fc983d0f3c5adf67a3ea6758a0e4fff1a3bf` |
| run6/judge-brief-blind6.md | judge brief | - | `9f80d2bad56ef000f200c616a5e6bb2fc307b9ee3327be4f035da3b9140d8675` |
| run6/run6.jsonl | records | 100 | `ef281290bb088ec94f246405c7e4048aff9835b8c09ec49c090d3dea040c54ef` |
| run7/blind7-items.json | items | 100 | `0413f7b64afbef8c0b580a8c9b34a582267cb7e852420a9c19bbc4c77a027832` |
| run7/blind7-judgements.json | judgements | 100 | `e8f861c5ba3aa2042dd98f14656d334b7f4fec96323d670e189914952333cfa8` |
| run7/blind7-key.json | key | 100 | `77f5bbbe9e7866673ca34b11b18c6831a5f8e0b031efdec37f91ad023c298a25` |
| run7/judge-brief-blind7.md | judge brief | - | `d92dd2eb2545004fae6578c968988cb1dd67d678046ab5f29c303d52d1f77b44` |
| run7/run7-tal.jsonl | records | 100 | `be4c1141cd179cb26886552635934050c43831c707b44f63fbd3f143f8d20b6a` |
| run8/blind8-items.json | items | 100 | `d68f59219459e253c13eaed839cc5b7730984afe1831b63a6dc9ad51a991f913` |
| run8/blind8-judgements.json | judgements | 100 | `8a16c4d9f9b5a5ed3b4a8c386206e60700f8dcdd9a993b1f589a11e4e89b7311` |
| run8/blind8-key.json | key | 100 | `51e17033210b54c859eda896fc3abd7567870919ab57cca12285bfebe811065f` |
| run8/judge-brief-blind8.md | judge brief | - | `ca16617e6ed15d6a526c6ee5da2caace7bd3c0a8c5b92272d7046780af06b240` |
| run8/run8-tal.jsonl | records | 100 | `5f926a5f159ab6020fcb3ac05ef0a966cb2c2b8ff8a9fe49b4ae100e3135be9e` |
| run8-models/blindm-key.json | key | 600 | `ab1da9f67382ab95f80216d8b1d1f17fa453439f9395a92dac95390c14dabee2` |
| run8-models/blindm0-items.json | items | 300 | `e1183a05ab06f708100e872ed855cf440e93d8a2d14a861a5bcd38aee0ab8158` |
| run8-models/blindm0-judgements.json | judgements | 300 | `1e258f27a467b4c6708010979fc8bd51f922a0cb0f9c8594fc7892d4227019cf` |
| run8-models/blindm1-items.json | items | 300 | `850440572cdb8d0acf7db66f18afa1cbf89b50602eb62fe9fe988e1a0365fff7` |
| run8-models/blindm1-judgements.json | judgements | 300 | `45ca0a9c489652162e7a32479e89384056c5f6b07c5d45996aed556f1f26e3a6` |
| run8-models/judge-brief-blindm0.md | judge brief | - | `70eb83a3efb68af62e2a781b61ca5b5ce8ae479f1358dafdf4616985def43d86` |
| run8-models/judge-brief-blindm1.md | judge brief | - | `744c71ce498b98e9534f14d7463cd89bd8608c3149b0a8cc23fe444abb6a647d` |
| run8-models/models-vram.txt | GPU sizes | - | `9a56160def662aac81a495bd26fb535a336140ae4dd53cea2bd8db81299efe3c` |
| run8-models/run8-caissa-m1-8b.jsonl | records | 50 | `bbf9f94ae353552d135d985332e394eeaabf809f1238f7deb578c934c2fff97e` |
| run8-models/run8-gemma3-1b.jsonl | records | 50 | `b7cfb0c69c0d7798d4099e3dd10e82dbe49bc8500de340dae3e6f2ca0bf958e1` |
| run8-models/run8-gemma3-270m.jsonl | records | 50 | `0a8d55093bb055fd1bea791e459b430a0126c8c6f1c95bf6f4e646bf604dd29e` |
| run8-models/run8-gemma4-e4b-q4ks.jsonl | records | 50 | `b2b1f6e2b9cdfe57fef180050991330b10e85ff9f25bd600098c21ab6fc6fd1b` |
| run8-models/run8-llama3.2-1b.jsonl | records | 50 | `59acad1d7237895d135f2c9e6d122f77fff70aa30a504ac155df089766e43672` |
| run8-models/run8-praxis-2b.jsonl | records | 50 | `e17304f62b8b23a9541a5e34bf89b31e64998c38d3890f0fcaf5dd8974666860` |
| run8-models/run8-praxis-4b.jsonl | records | 50 | `6aa5fa909acb9e90b57e6fb24342c13057bc76e1e87bb0f47b5864e6e94f4c4e` |
| run8-models/run8-qwen3-1.7b.jsonl | records | 50 | `58c05557e671b63358589ce0f9445a8f434a6b57ef55af296e6bfd23b7bb81c8` |
| run8-models/run8-qwen3.5-2b.jsonl | records | 50 | `a61a8e1c45f45f28821a750e9905c4bda75e0d53cb2eaad9f56d84fb751dab88` |
| run8-models/run8-qwen3.5-4b.jsonl | records | 50 | `32d6b46773ae55d4a7532ddddc66401df7a4103ba7ad3c81074b5c920eebe9f4` |
| run8-models/run8-second-2b.jsonl | records | 50 | `90f79e26405ae357ebee8d6d3ff32c0493827de5fe1b437cb000bbaed38718c1` |
| run9/blind9-items.json | items | 250 | `6841d9a58bad7038d7e42232aed7beaf4a374de9db930370548c9a4c90881af1` |
| run9/blind9-judgements.json | judgements | 250 | `1f09f461f6e1069dece4feadca884eb6d2465b6aa0cd50ac468e052c4045af8e` |
| run9/blind9-key.json | key | 250 | `c80076cd31954e321cc9b001feeabf39f2d596b42c015036bcffbe82ffc12df0` |
| run9/judge-brief-blind9.md | judge brief | - | `e3152cab63882aefa9b42071919cdc67a3d447e16d9a7e14dfe407fcb1d0e8de` |
| run9/run9-A-gemma.jsonl | records | 50 | `4b9de58542d387370b4d2c585a00a6e94ca81222cf2cdd299af4924d988c4771` |
| run9/run9-B-gemma-own.jsonl | records | 50 | `447d94d83755c2f537eee9422a37e642a591e0f610078664ac56a8b662d8056c` |
| run9/run9-C-praxis-t0.jsonl | records | 50 | `ff438ba89b99bd285e6c31e23ee6b50d35057bea7c0faa6fe8241023c298bc4a` |
| run9/run9-D-praxis-t1.jsonl | records | 50 | `a1ff55ba31cbec52b3948dabe2ca87c8287194395c3c37005f6c637936df9a53` |
| run9/run9-E-praxis-t1-own.jsonl | records | 50 | `4f534605775a50f579b8be53ec049c86884681b5d06930d1284c7780e6601cfa` |
| vlm/P1-greedy-knight.svg | board | - | `fab0132ce3b9d9abce82089399fbbfcd7ddc981ef9e1d90fa33a45aae015577d` |
| vlm/P2-boxed-king.svg | board | - | `f7e10dba68e21c834638accb1473cefa5a7d9cc46c35a9e2dae1772ca320262a` |
| vlm/P3-back-rank.svg | board | - | `fcbc167481d328b37b23339d046cc59e1909dd23088ccc2cc1bb61a574370023` |
| vlm/P4-fork.svg | board | - | `9c55d1ca4296ed2563213a4725eefc483bb7322964e178a61663632e8728bbca` |
| vlm/P5-shut-bishop.svg | board | - | `84854e466ded688e5fbfa62cfe806a923338ff4256349dfbdf4f56ccb7846b38` |

<!-- summarise:end -->

## Limits

- **P1–P5 tuned the prompts.** Only the run 9 positions are held out, and there are 5 of them.
- **The stored keys sat beside their items.** The judges were told not to open them, and nothing stopped them. New sets write the key outside the judge's folder.
- **One judge per set, and it is a model.** The judges of runs 2–8 were one session, so its later sets were judged with the rule decisions it made on the earlier ones.
- **The p95 column of run 9 differs from #462 for two cells.** This page uses the nearest rank (the value at index int(0.95 × n)), as the 12-model table did. The #462 comment used the value one place lower. Cell C reads 0.60 s here and 0.59 s on #462. Cell D reads 0.92 s here and 0.84 s on #462.
- **The records of runs 1–8 do not name their model.** `summarise.py` names it from #457. Run 9 records name the model and the options; new records carry the full `meta`.
- **GPU memory here is not measured as in [narrator-models.md](narrator-models.md).** This page uses Ollama's `size_vram` (from `/api/ps`) after each run, for example 2.43 GiB for Q3_K_M. That page measures the GPU's used memory before and after the model loads, and gives 2.96–3.13 GiB for the same model.
- **One stored example breaks the sentence check.** The run 7 Chernev example "Notice that the e-file lies wide open, waiting for a rook!" names a rook that its fact does not name. Run 7 ran Tal only, so the example never reached a stored prompt.
