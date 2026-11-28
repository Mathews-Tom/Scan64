# Evaluation trace fixtures

Each file is an engine's output for `uci`, `position fen <FEN>`, `eval` and
`quit`. The parsers in `src/scan64/providers/engine/trace.py` read them.
Every file is a real recording, not a hand-written copy.

Position 1 is
`r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4`;
position 2 is `2r3k1/5ppp/p3p3/1p1n4/3P4/P4N2/1P2RPPP/6K1 b - - 0 25`.
The file name ends in the position number.

## From the engine compatibility set (#397)

Recorded on 2026-11-28 from the binaries already built for #397.

| File | Engine |
|---|---|
| `stockfish11-1.txt`, `stockfish11-2.txt` | Stockfish 11 (classical only) |
| `fairy-stockfish14-1.txt`, `fairy-stockfish14-2.txt` | Fairy-Stockfish 14, no network loaded |
| `rodent-v1.2-1.txt`, `rodent-v1.2-2.txt` | Rodent V 1.2, run from its own folder |

## Built for the trace parsers (#478)

Built and recorded on 2026-11-28 on Linux with gcc 15.2.0. The UCI options
sent before `position fen` are listed in the Options column.

| Files | Engine | Commit | Build command | Options |
|---|---|---|---|---|
| `stockfish2.1-1.txt`, `-2.txt` | Stockfish 2.1 | tag `sf_2.1` (`315c583`) | `make build ARCH=x86-64-modern COMP=gcc` in `src` | none |
| `stockfish8-1.txt`, `-2.txt` | Stockfish 8 | tag `sf_8` (`369eff4`) | `make build ARCH=x86-64-modern COMP=gcc` in `src` | none |
| `stockfish15.1-1.txt`, `-2.txt` | Stockfish 15.1 | tag `sf_15.1` (`758f9c9`) | `make build ARCH=x86-64-avx2 COMP=gcc` in `src` | `Use NNUE` false |
| `rubichess-1.txt`, `-2.txt` | RubiChess 20261103 | `263f15d` | `make build` in `src` | `Use_NNUE` false |
| `vajolet2-1.txt`, `-2.txt` | Vajolet2 3.2.0 | tag `Vajolet2_3.2` (`ab0ce1e`) | `cmake -DVAJOLET_CPU_TYPE=64NEW -DCMAKE_BUILD_TYPE=Release ..` and `make Vajolet` in `build` | none |
| `laser-1.stdout.txt`, `laser-1.stderr.txt`, and the same for 2 | Laser 1.8 beta | `6e60f3a` | `make` in `src` | none |

Laser prints the table on standard error and the UCI lines on standard
output, so each Laser position has two files. The other engines print
nothing on standard error.
