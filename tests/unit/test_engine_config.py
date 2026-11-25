"""Engine selection by configuration, extended lines and capability handling."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest

from scan64.chess.analysis.models import EngineAnalysis, EngineAnalysisConfig
from scan64.chess.analysis.reuse import _key
from scan64.providers.engine import (
    ENGINE_CONFIG_ENV,
    STOCKFISH_PATH_ENV,
    EngineCapabilityError,
    EngineConfig,
    EngineConfigurationError,
    SearchLimits,
    active_engine_profile,
    line_from_info,
    load_engine_profile,
)
from scan64.providers.engine.uci import full_strength_options, startup_options
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig
from scan64.providers.stockfish.pool import (
    HINT_MULTIPV,
    HINT_NODES,
    HINT_TIME_MS,
    EnginePoolManager,
    PooledStockfishAdapter,
)

ETHEREAL_OPTIONS = ("Hash", "Threads", "EvalFile", "MultiPV", "Ponder", "UCI_Chess960")


def write_profile(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "engine.toml"
    path.write_text(body, encoding="utf-8")
    return path


def double(name: str, options: tuple[str, ...]) -> AsyncMock:
    engine = AsyncMock()
    engine.id = {"name": name}
    engine.config = {}
    engine.options = dict.fromkeys(options)
    return engine


def test_default_is_stockfish_and_honours_stockfish_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENGINE_CONFIG_ENV, raising=False)
    monkeypatch.setenv(STOCKFISH_PATH_ENV, "/opt/sf/stockfish")
    config = StockfishConfig()
    assert config is not None and type(config) is EngineConfig
    assert (config.name, config.binary_path, config.threads, config.hash_size) == (
        "stockfish",
        "/opt/sf/stockfish",
        1,
        16,
    )
    assert dict(config.options) == {} and dict(config.limits) == {}
    monkeypatch.delenv(STOCKFISH_PATH_ENV)
    assert StockfishConfig().binary_path == "stockfish"
    # Explicit keyword construction keeps its historical meaning.
    explicit = StockfishConfig(binary_path="sf", threads=2, hash_size=32)
    assert (explicit.binary_path, explicit.threads, explicit.hash_size) == ("sf", 2, 32)


def test_profile_selects_any_uci_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    binary = tmp_path / "ethereal.exe"
    path = write_profile(
        tmp_path,
        f"""
[engine]
name = "ethereal"
binary_path = '{binary}'
threads = 2
hash_mb = 64

[engine.options]
MoveOverhead = 10
Normalize = true

[limits.hint]
nodes = 50000
time_ms = 900
multipv = 3
""",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(path))
    monkeypatch.setenv(STOCKFISH_PATH_ENV, "ignored-when-a-profile-is-selected")
    config = EngineConfig()
    assert config.name == "ethereal"
    assert config.binary_path == str(binary)
    assert (config.threads, config.hash_size) == (2, 64)
    assert dict(config.options) == {"MoveOverhead": 10, "Normalize": True}
    assert config.limits["hint"] == SearchLimits(nodes=50_000, time_ms=900, multipv=3)
    assert active_engine_profile().source == path


def test_profile_is_reread_after_edit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = write_profile(tmp_path, '[engine]\nname = "a"\nbinary_path = "a"\n')
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(path))
    assert EngineConfig().name == "a"
    path.write_text('[engine]\nname = "bb"\nbinary_path = "bb"\n', encoding="utf-8")
    assert EngineConfig().name == "bb"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[engine]\nbinary_path = 'x'\n", "engine.name"),
        ("[engine]\nname = 'x'\n", "binary_path"),
        ("[engine]\nname = 'x'\nbinary_path = 'rel/x'\n", "absolute or a bare command"),
        ("[engine]\nname = 'x'\nbinary_path = 'x'\nthreads = 0\n", "engine.threads"),
        ("[engine]\nname = 'x'\nbinary_path = 'x'\ncolour = 1\n", "Unknown keys"),
        ("[engine]\nname = 'x'\nbinary_path = 'x'\n[engine.options]\nThreads = 4\n", "managed"),
        (
            "[engine]\nname = 'x'\nbinary_path = 'x'\n[engine.options]\n'Skill Level' = 3\n",
            "managed",
        ),
        ("[engine]\nname = 'x'\nbinary_path = 'x'\n[limits.review]\nnodes = 5\n", "Unknown"),
        ("[engine]\nname = 'x'\nbinary_path = 'x'\n[limits.hint]\nmultipv = 2\n", "nodes"),
        ("[engine]\nname = 'x'\nbinary_path = 'x'\n[limits.hint]\nnodes = -1\n", "positive"),
        ("[engine\n", "Invalid engine configuration"),
    ],
)
def test_invalid_profiles_fail_fast(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(EngineConfigurationError, match=message):
        load_engine_profile(write_profile(tmp_path, body))


def test_profile_path_must_exist_and_be_absolute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "missing.toml"))
    with pytest.raises(EngineConfigurationError, match="does not exist"):
        EngineConfig()
    with pytest.raises(EngineConfigurationError, match="absolute"):
        load_engine_profile(Path("relative.toml"))


def test_extended_line_fields_from_engine_info() -> None:
    board = chess.Board()
    board.push_uci("e2e4")
    info: chess.engine.InfoDict = {
        "pv": [chess.Move.from_uci("e7e5"), chess.Move.from_uci("g1f3")],
        "score": chess.engine.PovScore(chess.engine.Cp(-30), chess.WHITE),
        "wdl": chess.engine.PovWdl(chess.engine.Wdl(300, 600, 100), chess.WHITE),
        "depth": 14,
        "seldepth": 21,
        "nodes": 123_456,
        "nps": 987_654,
        "multipv": 2,
        "hashfull": 12,
    }
    # Black to move: scores and WDL are from Black's side.
    assert line_from_info(info, board.turn) == {
        "pv": ["e7e5", "g1f3"],
        "score_cp": 30,
        "wdl": [100, 600, 300],
        "depth": 14,
        "seldepth": 21,
        "nodes": 123_456,
        "nps": 987_654,
        "multipv": 2,
    }
    mate: chess.engine.InfoDict = {"score": chess.engine.PovScore(chess.engine.Mate(2), True)}
    assert line_from_info(mate, chess.WHITE) == {"score_mate": 2}


def test_legacy_rows_load_without_extended_fields() -> None:
    # The exact shape of rows stored before the line extension.
    legacy = EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 20}, {"score_mate": -3}])
    first, second = legacy.lines()
    assert first.pv == ["e2e4"] and first.score_cp == 20
    assert (first.wdl, first.depth, first.seldepth, first.nodes, first.nps, first.multipv) == (
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert second.pv == [] and second.score_mate == -3 and second.score_cp is None
    extended = EngineAnalysis(raw_result=[{"pv": ["d2d4"], "score_cp": 5, "wdl": [1, 998, 1]}])
    assert extended.lines()[0].wdl == (1, 998, 1)


def test_engine_identity_keys_never_cross_engines() -> None:
    def identity(name: str, digest: str, **options: object) -> dict[str, object]:
        return dict(
            EngineAnalysisConfig(
                engine_name=name, engine_version="sha256:" + digest * 64, options=options
            ).model_dump(),
            nodes=10_000,
        )

    stockfish = identity("Stockfish 18", "a", Threads=1, Hash=16, **{"Skill Level": 20})
    # Same reported name and options, different executable: still a different key.
    impostor = identity("Stockfish 18", "b", Threads=1, Hash=16, **{"Skill Level": 20})
    ethereal = identity("Ethereal 14.40", "c", Threads=1, Hash=16)
    keys = {_key(chess.STARTING_FEN, config) for config in (stockfish, impostor, ethereal)}
    assert None not in keys and len(keys) == 3


def test_startup_and_reset_send_only_declared_options() -> None:
    ethereal = double("Ethereal", ETHEREAL_OPTIONS)
    config = EngineConfig(binary_path="x", threads=2, hash_size=32, name="e", options={})
    assert startup_options(ethereal, config) == {"Threads": 2, "Hash": 32}
    assert full_strength_options(ethereal, 2) == {"Threads": 2}
    lc0 = double("Lc0", ("Threads", "WeightsFile", "MultiPV"))
    assert startup_options(lc0, config) == {"Threads": 2}
    unknown = EngineConfig(binary_path="x", name="e", options={"Contempt": 10})
    with pytest.raises(EngineConfigurationError, match="Contempt"):
        startup_options(ethereal, unknown)


@pytest.mark.asyncio
async def test_strength_features_fail_with_named_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scan64.chess.opponents.protocols import OpponentContext

    engine = double("Ethereal 14.40", ETHEREAL_OPTIONS)

    async def reject(options: dict[str, object]) -> None:
        unknown = [name for name in options if name not in engine.options]
        if unknown:
            raise chess.engine.EngineError(f"engine does not support option {unknown[0]}")

    engine.configure.side_effect = reject
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    binary = tmp_path / "ethereal"
    binary.write_bytes(b"ethereal test double")
    adapter = PooledStockfishAdapter(EngineConfig(binary_path=str(binary), name="e", options={}))
    try:
        with pytest.raises(EngineCapabilityError, match="Skill Level"):
            await adapter.play_move(chess.STARTING_FEN, 5, chess.engine.Limit(nodes=10))
        with pytest.raises(EngineCapabilityError, match="bot ladder requires .*Skill Level"):
            await adapter.play_bot_move(chess.STARTING_FEN, OpponentContext(0, bot_level=3))
    finally:
        await adapter.quit()


@pytest.mark.asyncio
async def test_per_call_adapter_rejects_undeclared_profile_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    engine = double("Ethereal", ETHEREAL_OPTIONS)
    monkeypatch.setattr(chess.engine, "popen_uci", AsyncMock(return_value=(None, engine)))
    binary = tmp_path / "ethereal"
    binary.write_bytes(b"ethereal test double")
    adapter = StockfishAdapter(
        EngineConfig(binary_path=str(binary), name="e", options={"Contempt": 1})
    )
    with pytest.raises(EngineConfigurationError, match="Contempt"):
        await adapter.analyze_position(chess.STARTING_FEN, nodes=10)
    engine.quit.assert_awaited_once()


@pytest.mark.asyncio
async def test_hint_limits_come_from_profile_and_key_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyze = AsyncMock(return_value=EngineAnalysis(raw_result=[{"pv": ["e2e4"], "score_cp": 0}]))
    monkeypatch.setattr(PooledStockfishAdapter, "ensure_started", AsyncMock())
    monkeypatch.setattr(PooledStockfishAdapter, "analyze_position", analyze)
    default = EnginePoolManager(EngineConfig(binary_path="sf", name="stockfish", limits={}))
    tuned = EnginePoolManager(
        EngineConfig(
            binary_path="sf",
            name="stockfish",
            limits={"hint": SearchLimits(nodes=40_000, time_ms=700, multipv=2)},
        )
    )
    try:
        await default.analyze_hint(chess.Board())
        assert analyze.await_args.kwargs == {
            "nodes": HINT_NODES,
            "time_ms": HINT_TIME_MS,
            "multipv": HINT_MULTIPV,
        }
        await tuned.analyze_hint(chess.Board())
        assert analyze.await_args.kwargs == {"nodes": 40_000, "time_ms": 700, "multipv": 2}
        assert default._hint_cache_key(chess.Board()) != tuned._hint_cache_key(chess.Board())
    finally:
        await default.close()
        await tuned.close()


@pytest.mark.parametrize(
    "profile", sorted((Path(__file__).parents[2] / "config" / "engines").glob("*.toml"))
)
def test_shipped_profiles_parse(profile: Path) -> None:
    parsed = load_engine_profile(profile.resolve())
    assert parsed.name and parsed.binary_path
