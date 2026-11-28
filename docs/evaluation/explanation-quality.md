# Explanation quality (#403)

Question: do explanations name the real reason a game was lost, and do they stay grounded?

Answer: with the pipeline at `4a77bdb` and gemma4-e4b Q4_K_S, the Tal card states the root cause in 21 of 23 explanations by the code metric (22 of 23 by the judge), and the turning point in 29 of 29. The judge calls 36 of 37 faithful. The same pipeline without the cause and the turning point (before #401) names them in 0 of 23 and 0 of 29.

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

Run 1, 2026-11-28, pipeline at `4a77bdb`, one Claude Sonnet judge for all 185 items. `summarise` rebuilds the tables, and a test checks that they match.

<!-- summarise:start -->

### Code metric, by model and voice

| Model | Voice | Accepted | Root cause stated | by the model | Turning point stated | Both stated | Grounding pass | First try | Coverage |
|---|---|---|---|---|---|---|---|---|---|
| plain | plain | 37/37 (100%) | 23/23 (100%) | 0/23 (0%) | 29/29 (100%) | 15/15 (100%) | - | - | 100% |
| scan64-bench-gemma4-e4b-q3km | Tal | 37/37 (100%) | 22/23 (96%) | 22/23 (96%) | 29/29 (100%) | 14/15 (93%) | 179/180 (99%) | 160/180 (89%) | 99% |
| scan64-bench-gemma4-e4b-q4ks | Chernev | 37/37 (100%) | 23/23 (100%) | 23/23 (100%) | 29/29 (100%) | 15/15 (100%) | 179/180 (99%) | 147/180 (82%) | 100% |
| scan64-bench-gemma4-e4b-q4ks | Tal | 37/37 (100%) | 21/23 (91%) | 21/23 (91%) | 29/29 (100%) | 14/15 (93%) | 179/180 (99%) | 159/180 (88%) | 95% |
| scan64-bench-gemma4-e4b-q4ks | no-cause | 37/37 (100%) | 1/23 (4%) | 1/23 (4%) | 0/29 (0%) | 0/15 (0%) | 127/128 (99%) | 118/128 (92%) | 95% |

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

<!-- summarise:end -->

## What it found

- The unfaithful Chernev sentences (3 of 37) give a White move to Black: "Black answers 18...d5" for 18.d5. The sentence check compares squares and moves and not the move's side (#511). The Tal card made none in these runs.
- The code metric and the judge disagree on 4 of 115 causes. Two are code misses ("backed up" for backward, "plodding pawn"), one is a king "under the watchful eyes" of pieces that the code accepts, and one is a line of the `no-cause` arm that repeats the king, its square and an attacker.
- The plain facts score 100% on the cause by construction. A model arm can only match them there; its gain is liveliness.

## Limits

- **The rater is a model, and no person has rated the set.** Its working was not kept: no transcript was available afterwards, so how it reached each answer cannot be checked. It judged 185 items in about two minutes and reported a script for bookkeeping. It gave `clear` 3 to 183 items and the same `lively` to every Chernev and plain comment, so treat those two columns as coarse.
- **The judge broke its brief on `order` twice.** It marked two comments as ordered (q004, q130) whose cause it marked as not named. The judgements are stored unchanged. The column counts a comment only when every fact it orders is named.
- **One check of the judge.** Three comments write a move with the wrong side's dots. A script can find these without the judge, and the judge had marked all 3 unfaithful. Nothing similar was measured for other kinds of error.
- **Small set.** 23 themes from 20 games, several from the same game, 1 explanation each. One explanation is 4 points. A difference of one or two explanations between arms is noise.
- **Themes.** `weak_pawns` and `unsafe_king` cover almost all; `passive_pieces` has one lesson and `lagging_development` none. The metric cannot tell if the stated theme is the true cause: that is #400's hand check.
- **Scores.** The themes use the Lichess server scores. The product uses its own fast pass (#486).
- **Run 1 predates `74ee9f5`.** That commit (#401) makes the check of the cause sentence stricter: the sentence must keep its fact's claim. Run 1 does not measure it.
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
python -m benchmarks.narrator.explanation summarise --doc docs/evaluation/explanation-quality.md
```

There is no seed option for the model: compare arms by their shares, never comment by comment. Judge all arms of one comparison with one judge.
