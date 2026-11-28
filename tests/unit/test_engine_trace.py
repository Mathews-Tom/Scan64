"""Evaluation traces: parsers, the profile declaration, the runner and the cross-check."""

from __future__ import annotations

import asyncio
from pathlib import Path

import chess
import chess.variant
import pytest

from scan64.explanations.factors import GROUPS, evaluate_factors
from scan64.explanations.trace_check import (
    FactorSources,
    main,
    render_report,
    sign_disagreements,
)
from scan64.providers.engine import EngineCapabilityError, EngineConfigurationError
from scan64.providers.engine.config import (
    TraceFormatName,
    active_engine_profile,
    load_engine_profile,
)
from scan64.providers.engine.trace import (
    EvalTrace,
    EvalTraceUnavailableError,
    TraceRow,
    parse_eval_trace,
    read_eval_trace,
)
from tests.support.engines import fake_trace_uci_launcher

TRACES = Path(__file__).resolve().parents[1] / "fixtures" / "engine_traces"
P1 = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
P2 = "2r3k1/5ppp/p3p3/1p1n4/3P4/P4N2/1P2RPPP/6K1 b - - 0 25"

# (fixture, format, row count, (row, total_mg, total_eg), evaluation for White)
CASES: list[tuple[Path, TraceFormatName, int, tuple[str, float, float | None], float | None]] = [
    (TRACES / "stockfish11-1.txt", "stockfish", 14, ("Threats", -0.33, -0.05), -0.44),
    (TRACES / "stockfish11-2.txt", "stockfish", 14, ("Pawns", -0.28, -0.23), -0.95),
    (TRACES / "fairy-stockfish14-1.txt", "stockfish", 15, ("King safety", 0.03, 0.05), -0.74),
    (TRACES / "fairy-stockfish14-2.txt", "stockfish", 15, ("Winnable", 0.0, 0.04), -0.77),
    (TRACES / "rodent-v1.2-1.txt", "rodent", 9, ("Threats", -0.31, -0.39), -0.28),
    (TRACES / "rodent-v1.2-2.txt", "rodent", 9, ("PST", -0.18, -0.54), -1.09),
    (TRACES / "stockfish2.1-1.txt", "stockfish", 14, ("Material, PST, Tempo", -0.23, -0.16), None),
    (TRACES / "stockfish2.1-2.txt", "stockfish", 14, ("Pawns", -0.35, -0.20), None),
    (TRACES / "stockfish8-1.txt", "stockfish", 13, ("Mobility", -0.08, -0.24), -0.35),
    (TRACES / "stockfish8-2.txt", "stockfish", 13, ("Knights", -0.33, -0.08), -0.77),
    (TRACES / "stockfish15.1-1.txt", "stockfish", 14, ("Winnable", 0.0, -0.11), -0.40),
    (TRACES / "stockfish15.1-2.txt", "stockfish", 14, ("Pawns", -0.14, -0.12), -0.37),
    (TRACES / "rubichess-1.txt", "rubichess", 11, ("King attacks", 0.06, 0.07), None),
    (TRACES / "rubichess-2.txt", "rubichess", 11, ("Pawns", -0.44, -0.22), None),
    (TRACES / "vajolet2-1.txt", "vajolet", 12, ("result", -0.249, -0.0308), None),
    (TRACES / "vajolet2-2.txt", "vajolet", 12, ("rooks", -0.284, -0.131), None),
    (TRACES / "laser-1.stderr.txt", "laser", 9, ("King Safety", 0.02, None), None),
    (TRACES / "laser-2.stderr.txt", "laser", 9, ("Pieces", -0.50, -0.32), None),
]


@pytest.mark.parametrize(
    ("path", "fmt", "count", "row", "evaluation"), CASES, ids=[case[0].name for case in CASES]
)
def test_parser_reads_each_fixture(
    path: Path,
    fmt: TraceFormatName,
    count: int,
    row: tuple[str, float, float | None],
    evaluation: float | None,
) -> None:
    trace = parse_eval_trace(fmt, path.read_text(), source="engine 1", fen=P1)
    assert len(trace.rows) == count
    found = next(r for r in trace.rows if r.name == row[0])
    assert found.total_mg == pytest.approx(row[1])
    if row[2] is None:
        assert found.total_eg is None
    else:
        assert found.total_eg == pytest.approx(row[2])
    assert trace.evaluation == (None if evaluation is None else pytest.approx(evaluation))
    assert set(trace.groups()) <= set(GROUPS)
    # Vajolet2 counts mobility inside each piece row: it has no Mobility row.
    assert {"Material", "King", "Threats"} <= set(trace.groups())


def test_stockfish11_rows_keep_both_sides_and_map_pieces() -> None:
    trace = parse_eval_trace(
        "stockfish", (TRACES / "stockfish11-1.txt").read_text(), source="sf", fen=P1
    )
    bishops = next(r for r in trace.rows if r.name == "Bishops")
    assert (bishops.white_mg, bishops.white_eg, bishops.black_mg, bishops.black_eg) == (
        -0.42,
        -0.96,
        -0.38,
        -1.14,
    )
    material = trace.rows[0]
    assert (material.white_mg, material.black_mg) == (None, None)
    pieces = trace.groups()["Pieces"]
    # Knights + Bishops + Rooks + Queens.
    assert pieces[0] == pytest.approx(0.05 - 0.04 + 0.0 + 0.0)
    assert pieces[1] == pytest.approx(-0.02 + 0.17 + 0.0 + 0.0)
    assert trace.groups()["Winnable"] == pytest.approx((0.0, -0.16))


def test_stockfish8_singular_bishop_row_joins_pieces() -> None:
    # Stockfish 8 prints "Bishop", not "Bishops".
    trace = parse_eval_trace(
        "stockfish", (TRACES / "stockfish8-1.txt").read_text(), source="sf", fen=P1
    )
    bishop = next(r for r in trace.rows if r.name == "Bishop")
    assert (bishop.total_mg, bishop.total_eg) == (-0.06, 0.0)
    # Knights 0.06 + Bishop -0.06 + Rooks 0 + Queens 0.
    assert trace.groups()["Pieces"] == pytest.approx((0.0, 0.0))


def test_rodent_values_are_converted_to_pawns() -> None:
    trace = parse_eval_trace(
        "rodent", (TRACES / "rodent-v1.2-1.txt").read_text(), source="r", fen=P1
    )
    pst = next(r for r in trace.rows if r.name == "PST")
    assert (pst.white_mg, pst.black_mg, pst.total_mg, pst.total_eg) == (-0.32, -0.06, -0.26, -0.45)
    # Material and PST both join the factor module's Material group.
    assert trace.groups()["Material"] == pytest.approx((-0.26, -0.45))


def test_output_without_a_table_is_a_capability_error() -> None:
    nnue_only = "id name Stockfish 16\nuciok\nNNUE evaluation        +0.25 (white side)\n"
    with pytest.raises(EvalTraceUnavailableError, match="no stockfish evaluation table"):
        parse_eval_trace("stockfish", nnue_only, source="Stockfish 16", fen=P1)
    assert issubclass(EvalTraceUnavailableError, EngineCapabilityError)
    assert EvalTraceUnavailableError.kind == "no_eval_trace"


def _profile(tmp_path: Path, binary: Path | str, trace: str) -> Path:
    path = tmp_path / "engine.toml"
    path.write_text(
        f"[engine]\nname = 'tracer'\nbinary_path = '{binary}'\n{trace}", encoding="utf-8"
    )
    return path


def test_profile_declares_the_trace_and_defaults_to_none(tmp_path: Path) -> None:
    profile = load_engine_profile(
        _profile(tmp_path, "x", "[trace]\nformat = 'rubichess'\noptions = { Threads = 1 }\n")
    )
    assert profile.trace is not None
    assert profile.trace.format == "rubichess"
    assert dict(profile.trace.options) == {"Threads": 1}
    assert load_engine_profile(_profile(tmp_path, "x", "")).trace is None
    assert active_engine_profile().trace is None


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[trace]\nformat = 'nnue'\n", "trace.format must be one of"),
        ("[trace]\n", "trace.format must be one of"),
        ("[trace]\nformat = 'laser'\nstream = 'stderr'\n", r"Unknown keys in \[trace\]"),
        ("[trace]\nformat = 'laser'\noptions = { X = 1.5 }\n", "trace.options.X must be"),
    ],
)
def test_profile_rejects_a_bad_trace_table(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(EngineConfigurationError, match=message):
        load_engine_profile(_profile(tmp_path, "x", body))


def test_profile_without_a_trace_raises_without_starting_an_engine(tmp_path: Path) -> None:
    profile = load_engine_profile(_profile(tmp_path, tmp_path / "missing-engine", ""))
    with pytest.raises(EvalTraceUnavailableError, match="declares no evaluation trace"):
        asyncio.run(read_eval_trace(profile, chess.Board(P1)))


@pytest.mark.parametrize(
    "board",
    [
        chess.Board("rnbqkbnr/ppppp1pp/8/5p1Q/4P3/8/PPPP1PPP/RNB1KBNR b KQkq - 1 2"),
        chess.variant.CrazyhouseBoard(),
    ],
    ids=["in-check", "variant"],
)
def test_positions_without_a_trace_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, board: chess.Board
) -> None:
    launcher, log = fake_trace_uci_launcher(tmp_path, monkeypatch, TRACES / "stockfish11-1.txt")
    profile = load_engine_profile(_profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\n"))
    with pytest.raises(EvalTraceUnavailableError):
        asyncio.run(read_eval_trace(profile, board))
    assert not log.exists()


def test_runner_switches_nnue_off_where_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, log = fake_trace_uci_launcher(
        tmp_path, monkeypatch, TRACES / "fairy-stockfish14-1.txt"
    )
    profile = load_engine_profile(_profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\n"))
    trace = asyncio.run(read_eval_trace(profile, chess.Board(P1)))
    assert trace.source == "Fairy-Stockfish 14"
    assert trace.fen == chess.Board(P1).fen()
    assert trace.evaluation == pytest.approx(-0.74)
    commands = log.read_text().splitlines()
    assert commands == [
        "uci",
        "setoption name Use NNUE value false",
        f"position fen {chess.Board(P1).fen()}",
        "eval",
        "quit",
    ]


def test_runner_sends_no_option_the_engine_lacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, log = fake_trace_uci_launcher(tmp_path, monkeypatch, TRACES / "stockfish11-2.txt")
    profile = load_engine_profile(_profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\n"))
    trace = asyncio.run(read_eval_trace(profile, chess.Board(P2)))
    assert trace.source == "Stockfish 11 64 POPCNT"
    assert not any(line.startswith("setoption") for line in log.read_text().splitlines())


@pytest.mark.parametrize("missing", ["engine", "folder/engine"])
def test_runner_names_a_missing_binary(tmp_path: Path, missing: str) -> None:
    binary = tmp_path / missing
    profile = load_engine_profile(_profile(tmp_path, binary, "[trace]\nformat = 'stockfish'\n"))
    with pytest.raises(EngineConfigurationError, match="cannot start its trace engine") as error:
        asyncio.run(read_eval_trace(profile, chess.Board(P1)))
    assert str(binary) in str(error.value)


def test_runner_rejects_an_undeclared_trace_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, _ = fake_trace_uci_launcher(tmp_path, monkeypatch, TRACES / "stockfish11-1.txt")
    profile = load_engine_profile(
        _profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\noptions = { Foo = 1 }\n")
    )
    with pytest.raises(EngineConfigurationError, match="trace option 'Foo'"):
        asyncio.run(read_eval_trace(profile, chess.Board(P1)))


def test_runner_reads_laser_from_standard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, _ = fake_trace_uci_launcher(
        tmp_path, monkeypatch, TRACES / "laser-1.stdout.txt", TRACES / "laser-1.stderr.txt"
    )
    profile = load_engine_profile(_profile(tmp_path, launcher, "[trace]\nformat = 'laser'\n"))
    trace = asyncio.run(read_eval_trace(profile, chess.Board(P1)))
    assert trace.source == "Laser 1.8 beta"
    assert trace.rows[-1].name == "Total"
    assert (trace.rows[-1].total_mg, trace.rows[-1].total_eg) == (-0.01, -0.23)


def test_runner_reports_an_engine_that_prints_no_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Rodent's output read as a Stockfish table: the profile names the wrong format.
    launcher, _ = fake_trace_uci_launcher(tmp_path, monkeypatch, TRACES / "rodent-v1.2-1.txt")
    profile = load_engine_profile(_profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\n"))
    with pytest.raises(EvalTraceUnavailableError, match="Rodent V 1.2 AVX2 printed no"):
        asyncio.run(read_eval_trace(profile, chess.Board(P1)))


def test_runner_times_out_and_stops_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher, _ = fake_trace_uci_launcher(
        tmp_path, monkeypatch, TRACES / "stockfish11-1.txt", hang=True
    )
    profile = load_engine_profile(_profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\n"))
    with pytest.raises(TimeoutError):
        asyncio.run(read_eval_trace(profile, chess.Board(P1), timeout_s=2.0))


def _trace(fen: str, groups: dict[str, tuple[float, float | None]]) -> EvalTrace:
    rows = tuple(
        TraceRow(name.lower(), None, None, None, None, mg, eg) for name, (mg, eg) in groups.items()
    )
    return EvalTrace(source="Engine 1.0", format="stockfish", fen=fen, rows=rows, evaluation=None)


def test_sign_disagreements_compare_each_group_and_phase() -> None:
    report = evaluate_factors(P2)
    mobility = report.groups["Mobility"]
    assert mobility.net_mg < 0 and mobility.net_eg < 0
    trace = _trace(
        report.fen,
        {
            # Opposite in the middle game only.
            "Mobility": (0.40, -0.20),
            # Below the epsilon: no sign, never a disagreement.
            "Space": (0.01, 0.01),
            # A missing end-game value is skipped.
            "King safety": (0.0, None),
        },
    )
    found = sign_disagreements(report, trace)
    assert [(d.group, d.phase, d.trace) for d in found] == [("Mobility", "middle game", 0.40)]
    assert found[0].source == "Engine 1.0"
    assert found[0].module == pytest.approx(mobility.net_mg / 206)


def test_winnable_row_is_compared_with_the_scale_factor() -> None:
    # The bonus is +0.28 pawns. Stockfish 15.1 prints Winnable -2.22 here, scale factor included.
    report = evaluate_factors("r3k1r1/pbpp1pbp/1p2p2p/2Q5/2P1P3/2P4N/PP3PPP/RN3RK1 b q - 0 13")
    winnable = report.groups["Winnable"].net_eg
    assert report.scale_factor < 64
    scaled = report.end_game * report.scale_factor / 64 - (report.end_game - winnable)
    assert winnable > 0.05 * 206 and scaled < -0.05 * 206

    # Stockfish 12 to 15.1 and Fairy-Stockfish: the row includes the scale factor.
    assert sign_disagreements(report, _trace(report.fen, {"Winnable": (0.0, -2.22)})) == []
    (found,) = sign_disagreements(report, _trace(report.fen, {"Winnable": (0.0, 0.5)}))
    assert (found.group, found.phase) == ("Winnable", "end game")
    assert found.module == pytest.approx(scaled / 206)
    assert found.module == pytest.approx(-2.22, abs=0.5)

    # Stockfish 11: the row leaves the scale factor out.
    assert sign_disagreements(report, _trace(report.fen, {"Initiative": (0.0, 0.5)})) == []
    (found,) = sign_disagreements(report, _trace(report.fen, {"Initiative": (0.0, -0.5)}))
    assert found.module == pytest.approx(winnable / 206)


def test_report_lists_disagreements_and_skips() -> None:
    report = evaluate_factors(P2)
    agreeing = evaluate_factors(P1)
    sources = [
        FactorSources(report, (_trace(report.fen, {"Mobility": (0.40, 0.40)}),)),
        FactorSources(agreeing, (_trace(agreeing.fen, {}),)),
    ]
    text = render_report(sources)
    assert "- Positions compared: 2" in text
    assert "- Positions with a disagreement: 1" in text
    assert f"| `{report.fen}` | Engine 1.0 | Mobility | middle game |" in text
    assert f"| `{report.fen}` | Engine 1.0 | Mobility | end game |" in text
    assert "No group disagrees" in render_report(sources[1:])


def test_report_command_runs_the_engine_double(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher, _ = fake_trace_uci_launcher(tmp_path, monkeypatch, TRACES / "stockfish11-1.txt")
    profile = _profile(tmp_path, launcher, "[trace]\nformat = 'stockfish'\n")
    fens = tmp_path / "fens.txt"
    check = "rnbqkbnr/ppppp1pp/8/5p1Q/4P3/8/PPPP1PPP/RNB1KBNR b KQkq - 1 2"
    fens.write_text(f"# positions\n{P1}\n\n{check}\n", encoding="utf-8")
    assert main(["--profile", str(profile), "--fens", str(fens)]) == 0
    out = capsys.readouterr().out
    assert "- Trace sources: Stockfish 11 64 POPCNT" in out
    assert "- Positions compared: 1" in out
    assert f"- `{check}`: Engines print no evaluation trace with a side in check" in out
