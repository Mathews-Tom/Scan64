# M94 motif operational-reference preregistration v2

Status: **source correction/preregistration only; v2 has not been measured**. The owner authorized exactly one new, disjoint fresh80, not a reanalysis of v1. The parent finalizes `tests/fixtures/m94/source_revisions_v2.json` and commits all frozen sources before any recording. Current v2 measurement identity remains unset and replay fails closed until actual artifacts receive independent byte pins.

## Historical boundary

Preserve the three `m94_fresh80_*` v1 artifacts, `source_revisions.json`, `m94-motif-protocol.md`, and every historical result row byte-for-byte. Reproduce v1 only at immutable data commit `fa6dc9c15b643df702e4dcc472cd9ceff66619a3` (preregistration `4871a5aa99166e72489e606e3e2f5a9c2c151cd4`). Current code is `m94-motifs-v2`, not a parallel v1 implementation. It rejects archived v1 replay; do not rescore consumed v1 labels, pool results, or tune corrected rules against v1. All historical 11-code reports, established corpus gates and M93 fork trust remain.

## Fixed selection and engine

Take exactly qualifying ordinals **361–440**, after original40, M78, M79, M93, and M94 v1. Verify the five previous Site-ID prefixes, including v1 ordinals 281–360, and reject duplicates or any overlap with those prior corpora. Reading prior IDs for disjointness is not inspecting or rescoring prior motif labels. Qualification remains: error-free PGN, both Elo 600–1600 inclusive, at least 20 plies. The source is the CC0 January 2013 rated-standard Lichess archive: `https://database.lichess.org/standard/lichess_db_standard_rated_2013-01.pgn.zst`. Its compressed SHA256 is `aa40b3671fa3cf1072eb182892cd90b0e1e003a4a5943492f64b77e7f3fd1635`. Hash an open binary descriptor, rewind, and pass that same descriptor to `zstd` on stdin; do not hash one path opening and decompress another.

Stockfish 19, one thread, 16 MB hash, `ucinewgame` before every position. Fast search: 10,000 nodes, MultiPV 1, existing 150 cp candidate threshold. Focused reply and independently searched deep source: 1,000,000 nodes, MultiPV 4. Use only the first deep source PV, never best-of-MultiPV label selection. Production motifs require a move swing of at least **200 integer centipawns**. Before opening the PGN source, creating an output PGN, or searching a position, reject an invalid/unbound preregistration, wrong thread/hash configuration, or engine identity different from either Stockfish 19 or the original recording. No new download, paid call, engine change, extension, restart, or extra sample.

## Operational definitions

Codes are `tactics.skewer`, `tactics.discovered_attack`, `tactics.back_rank`. Capabilities are respectively `m94-skewer-v2`, `m94-discovered-attack-v2`, and `m94-back-rank-v2`. Capability does not confer trust. These are conservative missed-source opportunities, not every chess position bearing the motif name. The source board must be valid; the actual played move must be legal and non-null, and differ from the recommendation. Castling is excluded. Replay every PV move legally; any malformed/illegal suffix rejects the entire line. Account for captures and promotions across the whole PV using pawn 1, knight/bishop 3, rook 5, queen 9. A material motif requires both full-PV net gain >=1 and gain >=1 in the consecutive target-square capture exchange beginning on ply three. Later unrelated gains do not repair an even target exchange.

- **Skewer:** the root bishop/rook/queen newly attacks a front enemy piece, either king or strictly more valuable than the rear enemy minor/rook/queen. After the root there is exactly that front piece between slider and rear on the same ray. The immediate reply moves the front away; ply three is the same slider capturing the same rear, with both positive gains. Pre-existing rays, pins, equal-value front/rear pairs, pawn rears, unrelated captures and even exchanges do not qualify.
- **Discovered attack:** a root blocker leaves a stationary friendly slider's ray. The root origin lies **strictly between** stationary slider and target, and the actual post-root board has a new attack on that target absent on the source board. This geometric condition includes EP clearing both mover and captured pawn; source-minus-origin alone is not the post-move position. An EP capture clearing another ray without its root origin on that segment is excluded. A non-king target is an enemy minor/rook/queen, remains on its square through the reply, and is captured by that stationary slider on ply three with both gains. For a king target require a positive mate-in-1–3 source score and a complete legal mating PV of exactly `2 * mate_in - 1` plies. Already open rays, moved sliders, castling, pawn targets and unsupported geometric threats are excluded.
- **Back rank:** the root rook/queen checks from the enemy king's home rank; the source score is mate in 1–3 with a complete legal mating PV of precisely that distance. At mate the defending king remains on its home rank, checked along that rank by a rook/queen. Each on-board forward-neighbor square (king file and adjacent files) is occupied by a defender. Root and final mating rook/queen may differ. Open flight, diagonal-only checks, wrong distance, truncated/unscored lines and non-home-rank mates do not qualify.

The independent oracle in `evaluation/m94_reference.py` imports no proof, evidence, composer, detector or shared operational predicate. It reconstructs legal positions, counts material separately, enumerates skewer rays in coordinates, applies discovery segment geometry plus actual new attacks, and independently checks mating confinement. It uses the separately searched deep source, not fast signals, reply PVs or evidence flags. This remains an engine-derived operational reference, not independent human-coach validation or proof of coaching causality.

## Immutable preregistration and one-shot recording

The new freeze maps exact source SHA256 values for the recorder, analysis inputs, proofs, detectors, independent oracle, replay, support, templates, selected tests and this protocol. The protocol module and freeze itself are additionally bound by the preregistration commit, not self-hashed. Load the freeze lazily only for M94 operations; importing historical helpers must not require it.

Supply the externally established **full 40-character immutable preregistration SHA**, never mutable HEAD. Preflight resolves that exact commit and compares its protocol module, freeze bytes, and every listed source byte against the working files and listed digests. A clean later commit is not authorization to substitute its protocol. Parent reviews/finalizes sources and freezes before invoking once:

```sh
BROWSER=/usr/bin/true uv run python scripts/record_diagnosis_reference.py \
  /tmp/m78-lichess-2013-01.pgn.zst --m94-v2-fresh80 \
  --preregistration-commit "$M94_V2_PREREGISTRATION_SHA"
```

Use disposable runtime DB/data. Outputs under `tests/fixtures/m48` are exclusively created `m94_v2_fresh80_real_games.pgn`, `m94_v2_fresh80_recorded.json`, and `m94_v2_fresh80_manifest.json`. Any existing output blocks recording. No overwrite, restart, re-search, delete-and-retry, or extension is permitted. A failed engine search or incomplete PGN/recording is **STOP**, retaining outputs for diagnosis.

### Bounded manifest finalization/recovery

After both complete outputs close, the recorder logs their SHA256 digests and preregistration SHA, then invokes `finalize_m94_manifest(commit, recording_sha256, pgn_sha256)`. The API verifies that same immutable source freeze, both supplied expected digests, full provenance, exactly 80 qualifying matching PGN/recording identities/moves, prior disjointness, one fast result per position, and focused/deep results for every candidate recomputed solely from saved fast output. It does not compute motif labels, launch an engine, read the archive, or repeat a search.

The manifest is fully written/flushed to a temporary sibling, then published by an exclusive hard link; it cannot leave a partially written target or overwrite an existing manifest. If only publication fails, complete original inputs remain. The sole recovery operation is finalizing those **same complete digest-bound outputs**, with the original logged digests and preregistration SHA:

```sh
BROWSER=/usr/bin/true uv run python scripts/record_diagnosis_reference.py \
  --finalize-m94-manifest --preregistration-commit "$M94_V2_PREREGISTRATION_SHA" \
  --recording-sha256 "$M94_V2_RECORDING_SHA256" --pgn-sha256 "$M94_V2_PGN_SHA256"
```

No source argument is accepted. Existing manifest, missing/changed/incomplete inputs, absent expected digests or changed frozen sources stop finalization. This is not a general retry framework and cannot recover partial measurement.

## Replay, decision and actual-data work

Before first v2 replay, parent pins the actual recording, PGN, manifest, protocol, freeze and preregistration identities independently in `M94_MEASUREMENT`. Hash and parse the same single-read recording/manifest/PGN buffers. Normalize corpus paths before validation, labeling and detector dispatch; relative paths must not bypass pins. Omit unmeasured M94 rows from original40/M78/M79 reports.

The gate is unchanged: **precision >=0.80 and support (TP+FP) >=10 per code**. No pooling, extra ordinals, threshold adjustment, tuning, or another recording. Promote only actual passing codes consistently across trust, saved consumers, Patterns, training, archive, explanations and puzzle mapping; hide failing codes. Established historical gates remain mandatory. Parent adds actual opaque-byte pins and exact-count regression expectations only after the authorized recording, then runs the data-dependent tests without skips or invented goldens. Until then, actual replay/artifact tests deliberately cannot pass; synthetic provenance, preflight, geometry, path and consumer tests do not require v2 measurements.
