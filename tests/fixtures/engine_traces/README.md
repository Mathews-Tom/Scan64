# Evaluation trace fixtures

Each file is an engine's output for `uci`, `position fen <FEN>`, `eval` and
`quit`. The parsers in `src/scan64/providers/engine/trace.py` read them.

## Recorded

Recorded on 2026-11-28 from the binaries in the engine compatibility set
(#397). Position 1 is
`r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4`;
position 2 is `2r3k1/5ppp/p3p3/1p1n4/3P4/P4N2/1P2RPPP/6K1 b - - 0 25`.

| File | Engine |
|---|---|
| `stockfish11-1.txt`, `stockfish11-2.txt` | Stockfish 11 (classical only) |
| `fairy-stockfish14-1.txt`, `fairy-stockfish14-2.txt` | Fairy-Stockfish 14, no network loaded |
| `rodent-v1.2-1.txt`, `rodent-v1.2-2.txt` | Rodent V 1.2, run from its own folder |

## Derived from source

The files in `from-source/` follow each engine's print code line by line.
Their numbers are made up, so they test the layout only, not the values.
Real recordings replace them when these engines are built on a test machine.

| File | Engine | Print code |
|---|---|---|
| `stockfish2.1.txt` | Stockfish 2.1 | `trace_evaluate`, `src/evaluate.cpp`, tag `sf_2.1` |
| `stockfish8.txt` | Stockfish 8 | `Eval::trace`, `src/evaluate.cpp`, tag `sf_8` |
| `stockfish15.1.txt` | Stockfish 15.1, `Use NNUE` false | `Eval::trace`, `src/evaluate.cpp`, tag `sf_15.1` |
| `rubichess.txt` | RubiChess, `Use_NNUE` false | `traceEvalOut`, `src/eval.cpp`, commit `263f15d` |
| `vajolet2.txt` | Vajolet2 3.2 | `Position::eval<true>`, `src/position/eval.cpp`, tag `Vajolet2_3.2` |
| `laser.stdout.txt`, `laser.stderr.txt` | Laser (table on standard error) | `EvalDebug::print`, `src/eval.cpp`, commit `6e60f3a` |
