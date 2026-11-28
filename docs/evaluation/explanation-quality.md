# Explanation quality (#403)

Question: do explanations name the real reason a game was lost, and do they stay grounded?

Answer, run 2 (pipeline at `2df3880`, with the stricter cause-sentence check of `74ee9f5`): the Tal card on gemma4-e4b Q4_K_S states the root cause in 23 of 23 explanations and the turning point in 29 of 29, by the code metric and by the judge. The model words 18 of 23 cause sentences; the other 5 use the plain fact. The judge calls 37 of 37 faithful.

Answer, run 1: with the pipeline at `4a77bdb` and gemma4-e4b Q4_K_S, the Tal card states the root cause in 21 of 23 explanations by the code metric (22 of 23 by the judge), and the turning point in 29 of 29. The judge calls 36 of 37 faithful. The same pipeline without the cause and the turning point (before #401) names them in 0 of 23 and 0 of 29.

The code is in `benchmarks/narrator/explanation/`. It uses the client, the renderer and the blind rules of the [voice benchmark](narrator-voice.md). The pipeline under test is the product's own `voice_facts`.

## Fixed positions

`positions.json` holds 37 lessons from the 20 slow-loss games of `benchmarks/factor_calibration/games.json` (public Lichess games, CC0; the loser is the learner).

- Candidates: every move of the loser that lost half a pawn or more by the Lichess scores and whose lesson has a root-cause theme, plus the first lesson of each game that has a turning point and no theme.
- Stockfish 18 (depth 18, 1 thread, 64 MB hash) gives the best line and the line after the played move, 6 plies each. The file records the engine's binary digest and its networks.
- A candidate is left out when the engine plays the same move, or when the move loses less than half a pawn at depth 18. The 8 left out are listed in the file.
- 23 lessons have a theme, 29 have a turning point and 15 have both. A run computes the themes from the game, so it measures the theme code of its own commit.

## Arms

Each arm is one model and one voice, one explanation for each lesson. Sampling: temperature 1, top_p 0.95, top_k 64, `num_ctx` 4096, thinking off.

| Voice | What runs |
|---|---|
| `Tal` | The product's card |
| `Chernev` | The Chernev card of voice runs 6-9, with two cause examples written for this benchmark |
| `no-cause` | The Tal card without the cause and the turning point: the story before #401 |
| `plain` | The fact texts with no model: what a sentence falls back to |

## Code metric

Every measure is computed from the stored sentences of the accepted explanations. An explanation is accepted unless the model server failed.

| Column | Meaning |
|---|---|
| Root cause stated | One sentence holds the details that identify the theme. Unsafe king: "king", its square, and an open file or an attacker's square. Weak pawns: "pawn", every square and every kind of weakness. Other themes: each of the learner's pieces and its square. |
| by the model | The same, in a sentence the model wrote (not the plain fact). |
| Turning point stated | One sentence holds the turning move and a word for the turn ("turn", "tilt", "level" and the like). |
| Both stated | Both, in lessons that have both. |
| Grounding pass | Sentences the model wrote that passed the code check in at most 3 tries, of all sentences it was asked for. The rest use the plain fact. |
| First try | The same, in one call. |
| Coverage | Share of the played move, the best move and the engine's reply that the explanation states in SAN. |

The grounding pass is the pipeline's own check. The judge's "faithful" is the independent one.

## Rubric

A fresh judge session gets a folder with only the items: position, facts with roles, comment. The arms are shuffled, and the key is outside the folder. It uses the [#458 rubric](narrator-voice.md#blind-judge-protocol) (`faithful` is "no unsupported claim"; `lively`, `clear` and `jargon` are "reads naturally") plus:

| Field | Yes when |
|---|---|
| `root_cause_named` | A club player learns which lasting weakness the cause states, in any words. Null without a cause. |
| `turning_point_named` | The comment says the game turned with that move. Null without a turning point. |
| `order` | Cause, then turning point, then the move and its line, none missing. |

The brief is in `results/run1/judge-brief.md`. `judge.join` refuses a missing item, an extra one, or an answer outside the rubric. A person can rate the same items file with the same rubric.

## Results

| gemma4-e4b, Tal card | Q4_K_S run 1 | Q4_K_S run 2 | Q3_K_M run 1 | Q3_K_M run 2 |
|---|---|---|---|---|
| Cause worded by the model | 23/23 | 18/23 | 23/23 | 17/23 |
| Cause, first try | 22/23 | 15/23 | 18/23 | 11/23 |
| Turning point worded by the model | 29/29 | 29/29 | 29/29 | 29/29 |
| Turning point, first try | 29/29 | 24/29 | 29/29 | 25/29 |
| Root cause stated (code metric) | 21/23 | 23/23 | 22/23 | 23/23 |
| Faithful (judge) | 36/37 | 37/37 | 37/37 | 35/37 |
| Lively (judge) | 2.03 | 2.00 | 1.92 | 2.00 |

`74ee9f5` changes the check and both cause examples of the Tal card, so run 2 measures the two together. The two runs have different judges.

### Run 1

Run 1, 2026-11-28, pipeline at `4a77bdb`, one Claude Sonnet judge for all 185 items. `summarise` rebuilds the tables, and a test checks that they match.

<!-- summarise:run1:start -->

### Code metric, by model and voice

| Model | Voice | Accepted | Root cause stated | by the model | Turning point stated | Both stated | Grounding pass | First try | Coverage |
|---|---|---|---|---|---|---|---|---|---|
| plain | plain | 37/37 (100%) | 23/23 (100%) | 0/23 (0%) | 29/29 (100%) | 15/15 (100%) | - | - | 100% |
| scan64-bench-gemma4-e4b-q3km | Tal | 37/37 (100%) | 22/23 (96%) | 22/23 (96%) | 29/29 (100%) | 14/15 (93%) | 179/180 (99%) | 160/180 (89%) | 99% |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 37/37 (100%) | 23/23 (100%) | 23/23 (100%) | 29/29 (100%) | 15/15 (100%) | 179/180 (99%) | 147/180 (82%) | 100% |
| scan64-bench-gemma4-e4b-q4ks | Tal | 37/37 (100%) | 21/23 (91%) | 21/23 (91%) | 29/29 (100%) | 14/15 (93%) | 179/180 (99%) | 159/180 (88%) | 95% |
| scan64-bench-gemma4-e4b-q4ks | no-cause | 37/37 (100%) | 1/23 (4%) | 1/23 (4%) | 0/29 (0%) | 0/15 (0%) | 127/128 (99%) | 118/128 (92%) | 95% |

### Cause and turning-point sentences, worded by the model

| Model | Voice | Cause | Cause, first try | Turning point | Turning point, first try |
|---|---|---|---|---|---|
| plain | plain | - | - | - | - |
| scan64-bench-gemma4-e4b-q3km | Tal | 23/23 (100%) | 18/23 (78%) | 29/29 (100%) | 29/29 (100%) |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 23/23 (100%) | 23/23 (100%) | 29/29 (100%) | 29/29 (100%) |
| scan64-bench-gemma4-e4b-q4ks | Tal | 23/23 (100%) | 22/23 (96%) | 29/29 (100%) | 29/29 (100%) |
| scan64-bench-gemma4-e4b-q4ks | no-cause | - | - | - | - |

### Blind judge, by model and voice

| Model | Voice | n | Root cause named | Turning point named | Cause, effect, line | Faithful | Root cause named and faithful | Lively (1-3) | Clear (1-3) | Jargon |
|---|---|---|---|---|---|---|---|---|---|---|
| plain | plain | 37 | 23/23 (100%) | 29/29 (100%) | 37/37 (100%) | 37/37 (100%) | 23/23 (100%) | 1.00 | 2.97 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q3km | Tal | 37 | 22/23 (96%) | 29/29 (100%) | 36/37 (97%) | 37/37 (100%) | 22/23 (96%) | 1.92 | 3.00 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 37 | 23/23 (100%) | 29/29 (100%) | 37/37 (100%) | 34/37 (92%) | 20/23 (87%) | 1.00 | 3.00 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q4ks | Tal | 37 | 22/23 (96%) | 29/29 (100%) | 36/37 (97%) | 36/37 (97%) | 21/23 (91%) | 2.03 | 3.00 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q4ks | no-cause | 37 | 0/23 (0%) | 0/29 (0%) | 0/37 (0%) | 35/37 (95%) | 0/23 (0%) | 1.97 | 2.97 | 0/37 (0%) |

### Code metric against the judge

| Field | Both yes | Code yes, judge no | Code no, judge yes | Both no | Agree |
|---|---|---|---|---|---|
| root_cause_named | 88 | 2 | 2 | 23 | 111/115 (97%) |
| turning_point_named | 116 | 0 | 0 | 29 | 145/145 (100%) |

<!-- summarise:run1:end -->

### Run 2

Run 2, 2026-11-28, pipeline at `2df3880`, the same positions, models and sampling, one new Claude Sonnet 5.5 judge for all 185 items, blind seed 514. The files are in `results/run2/`.

<!-- summarise:run2:start -->

### Code metric, by model and voice

| Model | Voice | Accepted | Root cause stated | by the model | Turning point stated | Both stated | Grounding pass | First try | Coverage |
|---|---|---|---|---|---|---|---|---|---|
| plain | plain | 37/37 (100%) | 23/23 (100%) | 0/23 (0%) | 29/29 (100%) | 15/15 (100%) | - | - | 100% |
| scan64-bench-gemma4-e4b-q3km | Tal | 37/37 (100%) | 23/23 (100%) | 17/23 (74%) | 29/29 (100%) | 15/15 (100%) | 172/180 (96%) | 153/180 (85%) | 98% |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 37/37 (100%) | 23/23 (100%) | 23/23 (100%) | 29/29 (100%) | 15/15 (100%) | 179/180 (99%) | 149/180 (83%) | 100% |
| scan64-bench-gemma4-e4b-q4ks | Tal | 37/37 (100%) | 23/23 (100%) | 18/23 (78%) | 29/29 (100%) | 15/15 (100%) | 174/180 (97%) | 158/180 (88%) | 95% |
| scan64-bench-gemma4-e4b-q4ks | no-cause | 37/37 (100%) | 1/23 (4%) | 1/23 (4%) | 2/29 (7%) | 0/15 (0%) | 127/128 (99%) | 111/128 (87%) | 93% |

### Cause and turning-point sentences, worded by the model

| Model | Voice | Cause | Cause, first try | Turning point | Turning point, first try |
|---|---|---|---|---|---|
| plain | plain | - | - | - | - |
| scan64-bench-gemma4-e4b-q3km | Tal | 17/23 (74%) | 11/23 (48%) | 29/29 (100%) | 25/29 (86%) |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 23/23 (100%) | 23/23 (100%) | 29/29 (100%) | 29/29 (100%) |
| scan64-bench-gemma4-e4b-q4ks | Tal | 18/23 (78%) | 15/23 (65%) | 29/29 (100%) | 24/29 (83%) |
| scan64-bench-gemma4-e4b-q4ks | no-cause | - | - | - | - |

### Blind judge, by model and voice

| Model | Voice | n | Root cause named | Turning point named | Cause, effect, line | Faithful | Root cause named and faithful | Lively (1-3) | Clear (1-3) | Jargon |
|---|---|---|---|---|---|---|---|---|---|---|
| plain | plain | 37 | 23/23 (100%) | 29/29 (100%) | 37/37 (100%) | 37/37 (100%) | 23/23 (100%) | 1.00 | 2.76 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q3km | Tal | 37 | 23/23 (100%) | 29/29 (100%) | 37/37 (100%) | 35/37 (95%) | 22/23 (96%) | 2.00 | 2.97 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 37 | 23/23 (100%) | 29/29 (100%) | 37/37 (100%) | 34/37 (92%) | 21/23 (91%) | 1.00 | 2.70 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q4ks | Tal | 37 | 23/23 (100%) | 29/29 (100%) | 37/37 (100%) | 37/37 (100%) | 23/23 (100%) | 2.00 | 2.81 | 0/37 (0%) |
| scan64-bench-gemma4-e4b-q4ks | no-cause | 37 | 0/23 (0%) | 0/29 (0%) | 0/37 (0%) | 36/37 (97%) | 0/23 (0%) | 2.00 | 2.84 | 0/37 (0%) |

### Code metric against the judge

| Field | Both yes | Code yes, judge no | Code no, judge yes | Both no | Agree |
|---|---|---|---|---|---|
| root_cause_named | 92 | 1 | 0 | 22 | 114/115 (99%) |
| turning_point_named | 116 | 2 | 0 | 27 | 143/145 (99%) |

<!-- summarise:run2:end -->

## What it found

Run 2:

- **The stricter check costs wording, not content.** The model words 18 of 23 causes (Q4_K_S) and 17 of 23 (Q3_K_M), where run 1 had 23 of 23. The plain fact fills the rest, so every explanation states its cause. The turning point needs more tries and never falls back.
- **The Chernev card does not change**: 23 of 23 and 29 of 29, all on the first try. It repeats the fact's words.
- **No change in liveliness is visible.** The judge gives every Tal and `no-cause` comment 2, and every Chernev and plain comment 1, so a small change cannot show.
- The judge marks 6 of 185 unfaithful: 3 Chernev comments give a White move to Black (#511), 2 Q3_K_M comments claim the castled king is safe, and 1 `no-cause` comment calls the capturing rook captured.
- The code metric and the judge disagree on 1 cause and 2 turning points, all in the `no-cause` arm, where the code accepts other uses of the words.

Run 1:

- The unfaithful Chernev sentences (3 of 37) give a White move to Black: "Black answers 18...d5" for 18.d5. The sentence check compares squares and moves and not the move's side (#511). The Tal card made none in these runs.
- The code metric and the judge disagree on 4 of 115 causes. Two are code misses ("backed up" for backward, "plodding pawn"), one is a king "under the watchful eyes" of pieces that the code accepts, and one is a line of the `no-cause` arm that repeats the king, its square and an attacker.
- The plain facts score 100% on the cause by construction. A model arm can only match them there; its gain is liveliness.

## Limits

- **Run 2's judge transcript is also not kept.** The harness left no file. `results/run2/judge-session.md` holds the two attempts, the final report and the counts. The judge took 98 seconds for 185 items, and it gave one `lively` value to each arm.
- **Judges drift between runs.** The plain arm does not use a model, yet its `clear` is 2.97 in run 1 and 2.76 in run 2. Do not compare `clear` across runs.
- **The rater is a model, and no person has rated the set.** Run 1's working was not kept: no transcript was available afterwards, so how it reached each answer cannot be checked. It judged 185 items in about two minutes and reported a script for bookkeeping. It gave `clear` 3 to 183 items and the same `lively` to every Chernev and plain comment, so treat those two columns as coarse.
- **The judge broke its brief on `order` twice.** It marked two comments as ordered (q004, q130) whose cause it marked as not named. The judgements are stored unchanged. The column counts a comment only when every fact it orders is named.
- **One check of the judge.** Three comments write a move with the wrong side's dots. A script can find these without the judge, and the judge had marked all 3 unfaithful. Nothing similar was measured for other kinds of error.
- **Small set.** 23 themes from 20 games, several from the same game, 1 explanation each. One explanation is 4 points. A difference of one or two explanations between arms is noise.
- **Themes.** `weak_pawns` and `unsafe_king` cover almost all; `passive_pieces` has one lesson and `lagging_development` none. The metric cannot tell if the stated theme is the true cause: that is #400's hand check.
- **Scores.** The themes use the Lichess server scores. The product uses its own fast pass (#486).
- **Run 1 predates `74ee9f5`.** That commit (#401) makes the check of the cause sentence stricter: the sentence must keep its fact's claim. Run 2 does.
- **The metric is strict on details and blind to meaning.** It has no synonym list, so a picture can miss; a sentence that holds the details and denies them would pass. It was fixed before the judge ran and not changed after.
- **Not the delivered rate.** An explanation also has a time budget in the product, and game analysis does not pass the game yet (#482).

## Rerun

Commit first: `run` refuses a dirty tree. No command writes under `results/`.

```sh
python -m benchmarks.narrator.explanation run --voice Tal --model scan64-bench-gemma4-e4b-q4ks:latest \
    --options temperature=1,top_p=0.95,top_k=64 --out q4ks-tal.jsonl
python -m benchmarks.narrator.explanation run --voice plain --out plain.jsonl
python -m benchmarks.narrator.explanation blind q4ks-tal.jsonl plain.jsonl --seed 1 --out judge/ --key-out keys/blind-key.json
python -m benchmarks.narrator.explanation positions --engine stockfish --out new-positions.json
python -m benchmarks.narrator.explanation summarise benchmarks/narrator/explanation/results/run2 --doc docs/evaluation/explanation-quality.md
```

There is no seed option for the model: compare arms by their shares, never comment by comment. Judge all arms of one comparison with one judge.
