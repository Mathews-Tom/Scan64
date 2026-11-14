# Lichess puzzle-theme agreement

## Pre-registration

Frozen before engine recording, diagnosis execution or agreement results. This is
an informational independent-source **label proxy**, not coach validation, a
claim of chess improvement, or a change to product trust. The independent blind
coach review remains **PENDING**.

### Sample and provenance

Use the first **N = 2,000** qualifying, valid, unique puzzles in official file
order, with rating 600–1800 inclusive, rating deviation ≤100, popularity ≥80 and
at least 100 plays (A9 filters). Choose N in advance to make ≥10 occurrences of
the mapped common theme plausible; neither theme nor emitted-code support is
guaranteed. Do not enlarge or reselect after seeing results. Valid means a valid
standard-chess FEN and a legal setup move. Duplicate IDs are skipped.

The [official CC0 database](https://database.lichess.org/#puzzles) source is
`https://database.lichess.org/lichess_db_puzzle.csv.zst`. On 2026-11-14 request
inclusive bytes **0–8388607** (8,388,608 bytes); sha256:
`b58e19f817b7a8889cd713654a59e8914c3e92f11987b0273e74fb30e7d38b96`.
The server returned HTTP 206, `Content-Range: bytes 0-8388607/309751173`,
Last-Modified `Sun, 01 Nov 2026 07:57:40 GMT`, ETag `"6ae6f0f4-12766d85"`.
These are observed download metadata, not a Lichess-published checksum. The
rolling download URL may change; do not substitute a new prefix silently.

`tests/fixtures/m77/puzzles.csv` retains only ID, FEN, `Blunder = Moves[0]`, the
four filter columns, and Themes. Its sha256 is
`fc43cc078553522a19fb2cd0089eb5a63ad0518220a93788b530157099970b35`;
the fixed N was reached after 4,180 source rows. Full provenance is committed in
`tests/fixtures/m77/provenance.json`. No continuations, player identities or game
URLs are needed. Regenerate selection with:

```sh
curl --fail --location --range 0-8388607 --output /tmp/puzzles-prefix.csv.zst \
  https://database.lichess.org/lichess_db_puzzle.csv.zst
uv run python scripts/select_lichess_theme_sample.py /tmp/puzzles-prefix.csv.zst
```

The prefix ends inside a zstd frame: streaming stops after N, not at the
truncated end. The prefix itself is not committed; the small CC0 subset is.
No engine analyses or diagnosis/theme cross-tabulations have yet been computed.

### Owner-side framing and pipeline

The database states that FEN precedes the opponent's setup move and that the
second move starts the solution. Here the human who played **Moves[0] is the
owner**, i.e. `chess.Board(FEN).turn`. Diagnose that actual blunder, not a
hypothetical failed puzzle-solver move. Themes concern the *other side's*
punishing continuation. This framing is faithful to Scan64's mistake pipeline;
it necessarily leaves some owner-opportunity codes unmapped.

Reuse the M48 recording protocol and settings: Stockfish default configuration
(Threads 1, Hash 16 MiB), `ucinewgame` before every recorded request, fast
10,000-node single-PV analyses immediately before and after the setup move,
`FastPassOrchestrator` with swing threshold 150 cp (strictly greater), and a
1,000,000-node MultiPV-4 focused reply analysis only for flagged setups.
Record once; commit raw scores and PVs with engine identity/settings and sample
checksum. No million-node reference-label pass is needed: labels here come only
from the source Themes, never the engine. Engine evidence is not a human label.

Replay exactly those requests offline, use `compose_candidate_evidence` with
source FEN as `initial_fen`, the setup move as the one-move SAN history, normal
registered detectors and **joint** `arbitrate_diagnoses(..., trusted_only=True)`.
Emit at most one winning trusted code per row, as production does. M48 measures
each detector in isolation; that is deliberately *not* the estimand here. No
history before the supplied FEN is invented. A setup not flagged by the normal
fast pass remains in the sample denominator as an abstention. UUIDs/timestamps
are not report inputs. Do not change detectors, arbitration or `TRUSTED_CODES`.

### Frozen code-to-theme mapping

Definitions are from `learning/diagnosis/taxonomy/seeds.py`; implementations are
`detectors/board_awareness.py`, `detectors/threat_processing.py` and
`learning/evidence/composer.py` (rule `m48-fast-local-v1`). A mapped match means
any registered theme in the set is present. Absence is disagreement with this
proxy, not proof the diagnosis is false.

| Trusted code | Confirming theme set | Definition-tied rationale |
| --- | --- | --- |
| `board_awareness.hanging_piece` | `{hangingPiece}` | The owner loses a non-pawn piece in a profitable immediate local capture; Lichess hangingPiece describes the opponent's free capture of an undefended/underdefended piece. |
| `threat_processing.missed_check` | **unmapped** | The owner missed their own winning check *before* the blunder; mate/check themes describe the opponent's continuation and cannot confirm that missed opportunity. |
| `threat_processing.missed_capture` | **unmapped** | The owner missed taking an opponent piece *before* the blunder; hangingPiece in the opponent's continuation has the opposite ownership and is not confirming evidence. |
| `threat_processing.missed_direct_threat` | `{hangingPiece}` | The owner ignores a material threat immediately executed by a profitable capture; an opponent's hangingPiece continuation is a narrow overlapping material-loss proxy, not exhaustive threat coverage. |

No broad tags (`advantage`, `crushing`, `short`, `mate`, etc.) are promoted to
confirming labels. Hanging-piece and direct-threat meanings overlap; the proxy
cannot establish that an attack existed before the setup move, nor distinguish
these causes. Scan64's actual direct-threat evidence checks the immediate reply,
not the threat's history. Lichess may tag only a subset of profitable captures;
this mismatch must be interpreted as a limitation, not repaired post hoc.

### Theme-label provenance

[Lichess's database documentation](https://database.lichess.org/#puzzles) says
puzzles are automatically tagged and player votes refine their tags. Verified
source snapshots:

- [lichess-puzzler tagger at 8e24b6d](https://github.com/ornicar/lichess-puzzler/blob/8e24b6d5e2b9618e41581a327926e4ac67c610a2/tagger/cook.py):
  `cook` calls `hanging_piece` and emits `hangingPiece`. That rule inspects the
  punishing side's first capture of a non-pawn piece, hanging status, and local
  continuation material; it excludes some setup exchanges.
- [Tagger persistence](https://github.com/ornicar/lichess-puzzler/blob/8e24b6d5e2b9618e41581a327926e4ac67c610a2/tagger/tagger.py):
  automatic tags are submitted as `+theme` entries in the `lichess:<id>` voting
  round with `e: 100`, not an independent human judgement.
- [Lila theme policy](https://github.com/lichess-org/lila/blob/master/modules/puzzle/src/main/PuzzleTheme.scala)
  lists non-votable `staticThemes`: advantage, castling, crushing, enPassant,
  endgame, equality, long, master, masterVsMaster, superGM, mate, mateIn1–5,
  middlegame, oneMove, opening, short, smotheredMate, veryLong, checkFirst.
  These are locked/non-player-votable metadata. **hangingPiece is not static**:
  it is automatically seeded but unlocked/vote-refinable. Other tactical tags
  outside that list are likewise vote-refinable; no claim of human-only labels.
- [Theme descriptions](https://github.com/lichess-org/lila/blob/master/translation/source/puzzleTheme.xml)
  explicitly describe hangingPiece as an undefended or insufficiently defended
  opponent piece free to capture.

The CSV has no per-row voting history or tagger version. Whether any particular
retained tag was actually selected/changed by human votes is **unknown**. Source
inspection verifies mechanisms, not the historical origin of each CSV label.
The labels are independent of Scan64, but partly algorithmic and based on
engine-selected puzzles, so this is not fully independent chess ground truth.

### Frozen metrics and interpretation

Denominator: all 2,000 rows, with no exclusions after fast-pass screening.
For each of the four trusted codes report emitted support `s`, mapped-theme
positive rows `m`, and overlap `t`:

- Precision = `P(mapped theme present | code emitted) = t/s`; support is **code
  emissions**, not the number of puzzles carrying a tag. Report a Wilson 95%
  binomial CI using `z = 1.959963984540054`. For zero support show N/A, not 0.
- Recall = `P(code emitted | mapped theme present) = t/m`; a mapped-theme row
  counts as missed even if the pipeline abstains or emits a competing code.
  For zero theme positives show N/A. Unmapped codes have N/A precision, CI,
  recall and interpretation, but still report their emitted support.
- A4's precision ≥0.80 and support ≥10 is **informational only**. Apply it to
  the point estimate of mapped codes, not the CI bound; show pass/below/low
  support explicitly. Do not promote/demote product codes or fail CI based on it.
- Report counts/rates over N for: not fast-pass-flagged; flagged with no eligible
  diagnosis; flagged with only eligible untrusted diagnoses; emitted trusted
  diagnosis. The first three sum to the normal trusted pipeline's abstention
  rate. Identify untrusted-only rows by joint arbitration with
  `trusted_only=False` only when trusted arbitration abstains; they never count
  as production emissions. Also report total flagged count.
- Confusion/co-occurrence table: rows are the four winning trusted codes and
  the three abstention categories; columns are the **10 most frequent themes
  in the full frozen sample**, sorted by decreasing count then theme key.
  Cells count simultaneous row disposition and theme presence, not exclusive
  classes. Add sample theme totals; multiple tags can count for one puzzle.

All arithmetic and table ordering are deterministic. The CLI's default stdout
will be the entire `## Results` suffix committed below, byte-for-byte;
`--check` compares that suffix with offline recomputation and exits nonzero only
on fixture/report inconsistency, **never** on informational A4 outcomes. It
must not run Stockfish, download data, contact a model or access a database.

## Results: pending

Awaiting owner approval of the frozen mapping and N before any engine recording
or agreement computation.
