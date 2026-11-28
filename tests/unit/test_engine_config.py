"""Engine selection by configuration, extended lines and capability handling."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import chess
import chess.engine
import pytest

from scan64.chess.analysis.models import EngineAnalysis, EngineAnalysisConfig, EngineLine
from scan64.chess.analysis.reuse import _key
from scan64.learning.tolerance import win_probability
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
from scan64.providers.engine.config import (
    LADDER_ENGINE_CONFIG_ENV,
    LadderSettings,
    ladder_engine_config,
    ladder_engine_profile,
    scaled_nodes,
)
from scan64.providers.engine.uci import full_strength_options, lines_from_result, startup_options
from scan64.providers.stockfish.adapter import StockfishAdapter, StockfishConfig, search_limit
from scan64.providers.stockfish.identity import analysis_identity
from scan64.providers.stockfish.pool import (
    HINT_MULTIPV,
    HINT_NODES,
    HINT_TIME_MS,
    EnginePoolManager,
    PooledStockfishAdapter,
)

ETHEREAL_OPTIONS = ("Hash", "Threads", "EvalFile", "MultiPV", "Ponder", "UCI_Chess960")


ENGINE = "[engine]\nname = 'x'\nbinary_path = 'x'\n"
ELO_LADDER = "[ladder]\nstrength = 'elo'\nelo = [1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]\n"


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
        ("[engine]\nname = 'x'\nbinary_path = 'x'\n[limits.hint]\ndepth = 9\n", "Unknown"),
        ("[engine\n", "Invalid engine configuration"),
        (f"{ENGINE}[ladder]\nstrength = 'nodes'\n", "ladder.strength must be one of"),
        (f"{ENGINE}[ladder]\nelo = [1000]\n", "applies only to"),
        (f"{ENGINE}[ladder]\nstrength = 'elo'\nelo = [1000]\nmovetime_ms = 5\n", "8 integers"),
        (f"{ENGINE}{ELO_LADDER}movetime_ms = 0\n", "ladder.movetime_ms"),
        (f"{ENGINE}{ELO_LADDER}", "ladder.movetime_ms"),
        (
            f"{ENGINE}[ladder]\nstrength = 'elo'\nelo = [1, 2, 3, 4, 5, 6, 6, 8]\n"
            "movetime_ms = 5\n",
            "increase",
        ),
        (f"{ENGINE}{ELO_LADDER}movetime_ms = 5\ndepth = 3\n", r"Unknown keys in \[ladder\]"),
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


def test_node_scale_parses_validates_and_scales(tmp_path: Path) -> None:
    profile = load_engine_profile(
        write_profile(tmp_path, "[engine]\nname = 'lc0'\nbinary_path = 'lc0'\nnode_scale = 0.002\n")
    )
    assert profile.node_scale == 0.002
    assert (
        load_engine_profile(
            write_profile(tmp_path, "[engine]\nname = 'x'\nbinary_path = 'x'\n")
        ).node_scale
        == 1.0
    )
    for bad in ("0", "1.5", "-0.1", "true", "'half'"):
        with pytest.raises(EngineConfigurationError, match="node_scale"):
            load_engine_profile(
                write_profile(
                    tmp_path, f"[engine]\nname = 'x'\nbinary_path = 'x'\nnode_scale = {bad}\n"
                )
            )
    assert scaled_nodes(1_000_000, 0.002) == 2_000
    assert scaled_nodes(10, 0.001) == 1
    assert scaled_nodes(None, 0.5) is None
    assert search_limit(10_000, None, None, node_scale=0.5).nodes == 5_000


def test_node_scale_is_part_of_identity_only_when_not_one() -> None:
    engine = double("Lc0", ("Threads", "MultiPV"))
    raw = {"engine_name": "Lc0", "engine_version": "sha256:" + "d" * 64, "network": None}
    default = analysis_identity(engine, raw, 1, 1.0, 1.0)
    scaled = analysis_identity(engine, raw, 1, 0.002, 1.0)
    # Rows written before node_scale existed keep their exact reuse key.
    assert "node_scale" not in default
    assert scaled["node_scale"] == 0.002
    keys = {
        _key(chess.STARTING_FEN, dict(c, nodes=10_000, depth=None, time_ms=None, multipv=1))
        for c in (default, scaled)
    }
    assert None not in keys and len(keys) == 2
    assert (
        EngineConfig(binary_path="x", name="e", node_scale=0.5).fingerprint()
        != EngineConfig(binary_path="x", name="e", node_scale=1.0).fingerprint()
    )


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


def test_strength_reset_stays_inside_the_declared_range() -> None:
    def spin(name: str, default: int, low: int, high: int) -> chess.engine.Option:
        return chess.engine.Option(name, "spin", default, low, high, [])

    stockfish_11 = double("Stockfish 11", ("Threads", "Skill Level", "UCI_LimitStrength"))
    stockfish_11.options["UCI_Elo"] = spin("UCI_Elo", 1350, 1350, 2850)
    stockfish_11.options["Skill Level"] = spin("Skill Level", 20, 0, 20)
    assert full_strength_options(stockfish_11, 1) == {
        "Threads": 1,
        "Skill Level": 20,
        "UCI_LimitStrength": False,
    }
    capped = double("Capped", ())
    capped.options["Skill Level"] = spin("Skill Level", 10, 0, 10)
    assert full_strength_options(capped, 1) == {"Skill Level": 10}


@pytest.mark.asyncio
async def test_uci_engine_without_multipv_refuses_multiple_lines() -> None:
    from scan64.providers.engine.uci import EngineCapabilityError
    from scan64.providers.stockfish.adapter import search

    alexandria = double("Alexandria 9.1.0", ("Hash", "Threads", "Minimal", "UCI_Chess960"))
    limit = chess.engine.Limit(nodes=1000)
    with pytest.raises(EngineCapabilityError, match="3 analysis lines need the UCI option MultiPV"):
        await search(alexandria, chess.Board(), limit, 3, deadline_s=5, score_scale=1.0)
    alexandria.analyse.assert_not_awaited()
    alexandria.analyse.return_value = {"pv": [chess.Move.from_uci("e2e4")]}
    assert await search(alexandria, chess.Board(), limit, 1, deadline_s=5, score_scale=1.0) == [
        {"pv": ["e2e4"]}
    ]


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


def test_explicit_binary_ignores_a_non_stockfish_profile_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = write_profile(
        tmp_path,
        """
[engine]
name = "ethereal"
binary_path = "ethereal"
threads = 2
hash_mb = 64

[engine.options]
EvalFile = "net.bin"

[limits.hint]
nodes = 1000
""",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(path))
    # The calibrate path: an explicit Stockfish binary under an Ethereal profile.
    explicit = StockfishConfig(binary_path="/opt/sf/stockfish", threads=1, hash_size=16)
    assert (explicit.name, dict(explicit.options), dict(explicit.limits)) == ("stockfish", {}, {})
    assert (explicit.threads, explicit.hash_size) == (1, 16)
    # Unset resources still follow the profile; explicit identity fields win.
    partial = StockfishConfig(binary_path="/opt/sf/stockfish", name="sf", options={"Hash": 8})
    assert (partial.threads, partial.hash_size, partial.name) == (2, 64, "sf")
    assert dict(partial.options) == {"Hash": 8}
    profile = StockfishConfig()
    assert (profile.binary_path, profile.name, dict(profile.options)) == (
        "ethereal",
        "ethereal",
        {"EvalFile": "net.bin"},
    )
    assert "hint" in profile.limits
    assert StockfishConfig() == StockfishConfig()


@pytest.mark.asyncio
async def test_node_search_past_its_deadline_is_a_capability_error(tmp_path: Path) -> None:
    from scan64.providers.engine.uci import EngineCapabilityError
    from scan64.providers.stockfish.adapter import search

    assert (
        load_engine_profile(
            write_profile(
                tmp_path, "[engine]\nname = 'x'\nbinary_path = 'x'\nsearch_deadline_s = 30\n"
            )
        ).search_deadline_s
        == 30.0
    )
    for bad in ("0", "inf", "nan", "true"):
        with pytest.raises(EngineConfigurationError, match="engine.search_deadline_s"):
            load_engine_profile(
                write_profile(
                    tmp_path,
                    f"[engine]\nname = 'x'\nbinary_path = 'x'\nsearch_deadline_s = {bad}\n",
                )
            )

    async def never_finishes(*args: object, **kwargs: object) -> object:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    rodent = double("Rodent V 1.2", ("Hash", "Threads", "MultiPV"))
    rodent.analyse.side_effect = never_finishes
    with pytest.raises(
        EngineCapabilityError,
        match="Rodent V 1.2 did not finish a search limited to 10000 nodes within 0.05 s",
    ):
        await search(
            rodent,
            chess.Board(),
            chess.engine.Limit(nodes=10_000),
            1,
            deadline_s=0.05,
            score_scale=1.0,
        )
    rodent.analyse.side_effect = None
    rodent.analyse.return_value = {"pv": [chess.Move.from_uci("e2e4")]}
    timed = chess.engine.Limit(nodes=10_000, time=0.1)
    assert await search(rodent, chess.Board(), timed, 1, deadline_s=0.05, score_scale=1.0) == [
        {"pv": ["e2e4"]}
    ]


@pytest.mark.asyncio
async def test_pool_replaces_an_engine_that_terminated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from scan64.providers.stockfish.pool import EnginePool

    engines: list[AsyncMock] = []

    async def start_engine(path: str) -> tuple[None, AsyncMock]:
        engine = double(f"Engine {len(engines)}", ("Threads", "Hash", "MultiPV"))
        if not engines:
            engine.analyse.side_effect = chess.engine.EngineTerminatedError("engine process died")
        else:
            engine.analyse.return_value = {"pv": [chess.Move.from_uci("e2e4")]}
        engines.append(engine)
        return None, engine

    monkeypatch.setattr(chess.engine, "popen_uci", start_engine)
    binary = tmp_path / "engine"
    binary.write_bytes(b"deterministic UCI test double")
    pool = EnginePool(StockfishConfig(binary_path=str(binary)), concurrency=1)
    try:
        with pytest.raises(chess.engine.EngineTerminatedError):
            async with pool.acquire() as adapter:
                await adapter.analyze_position(chess.STARTING_FEN, nodes=100)
        async with pool.acquire() as adapter:
            analysis = await adapter.analyze_position(chess.STARTING_FEN, nodes=100)
        assert analysis.raw_result == [{"pv": ["e2e4"]}]
        assert len(engines) == 2
    finally:
        await pool.close()


def test_explicit_binary_takes_stockfish_protocol_scale_and_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = write_profile(
        tmp_path,
        "[engine]\nname = 'lc0'\nbinary_path = 'lc0'\nprotocol = 'xboard'\n"
        "node_scale = 0.002\nsearch_deadline_s = 30\nscore_scale = 2.8\n\n"
        "[ladder]\nstrength = 'elo'\nelo = [1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]\n"
        "movetime_ms = 500\n",
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(path))
    assert StockfishConfig().score_scale == 2.8
    assert StockfishConfig().ladder.strength == "elo"
    stockfish = StockfishConfig(binary_path="/opt/sf/stockfish")
    assert (stockfish.score_scale, stockfish.ladder.strength) == (1.0, "skill")
    # scan64 calibrate --stockfish: an explicit Stockfish binary under this profile.
    explicit = StockfishConfig(binary_path="/opt/sf/stockfish")
    assert (explicit.protocol, explicit.node_scale, explicit.search_deadline_s) == (
        "uci",
        1.0,
        120.0,
    )
    profile = StockfishConfig()
    assert (profile.protocol, profile.node_scale, profile.search_deadline_s) == (
        "xboard",
        0.002,
        30.0,
    )
    given = StockfishConfig(binary_path="/opt/sf/stockfish", node_scale=0.5, protocol="xboard")
    assert (given.node_scale, given.protocol) == (0.5, "xboard")


def test_ladder_defaults_to_skill_and_parses_elo(tmp_path: Path) -> None:
    assert load_engine_profile(write_profile(tmp_path, ENGINE)).ladder == LadderSettings()
    ladder = load_engine_profile(
        write_profile(tmp_path, f"{ENGINE}{ELO_LADDER}movetime_ms = 400\n")
    ).ladder
    assert (ladder.strength, ladder.movetime_ms) == ("elo", 400)
    assert [ladder.elo_for(level) for level in (1, 8)] == [1000, 2500]
    with pytest.raises(EngineConfigurationError, match="under skill"):
        LadderSettings().elo_for(1)


def test_score_scale_parses_and_scales_win_probability(tmp_path: Path) -> None:
    assert load_engine_profile(write_profile(tmp_path, ENGINE)).score_scale == 1.0
    scaled = load_engine_profile(write_profile(tmp_path, ENGINE + "score_scale = 2.8\n"))
    assert scaled.score_scale == 2.8
    for bad in ("0", "-1", "true", "'2'"):
        with pytest.raises(EngineConfigurationError, match="engine.score_scale"):
            load_engine_profile(write_profile(tmp_path, f"{ENGINE}score_scale = {bad}\n"))
    info: chess.engine.InfoDict = {
        "pv": [chess.Move.from_uci("e2e4")],
        "score": chess.engine.PovScore(chess.engine.Cp(280), chess.WHITE),
    }
    assert lines_from_result(info, chess.WHITE, score_scale=1.0) == [
        {"pv": ["e2e4"], "score_cp": 280}
    ]
    [arasan] = lines_from_result(info, chess.WHITE, score_scale=2.8)
    assert arasan == {"pv": ["e2e4"], "score_cp": 280, "score_scale": 2.8}
    assert win_probability(arasan) == pytest.approx(win_probability({"score_cp": 100}))
    assert EngineLine.model_validate(arasan).score_scale == 2.8


def test_score_scale_is_part_of_identity_only_when_not_one() -> None:
    engine = double("Arasan", ("Threads", "MultiPV"))
    raw = {"engine_name": "Arasan", "engine_version": "sha256:" + "e" * 64, "network": None}
    default = analysis_identity(engine, raw, 1, 1.0, 1.0)
    scaled = analysis_identity(engine, raw, 1, 1.0, 2.8)
    assert "score_scale" not in default
    assert scaled["score_scale"] == 2.8
    assert StockfishConfig(binary_path="x", score_scale=2.8).fingerprint() != (
        StockfishConfig(binary_path="x", score_scale=1.0).fingerprint()
    )


def test_ladder_profile_is_the_analysis_engine_until_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analysis_path = tmp_path / "analysis.toml"
    analysis_path.write_text(
        "[engine]\nname = 'caissa'\nbinary_path = 'caissa'\n", encoding="utf-8"
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(analysis_path))
    monkeypatch.delenv(LADDER_ENGINE_CONFIG_ENV, raising=False)
    analysis = EngineConfig()
    assert ladder_engine_profile() is None
    assert ladder_engine_config(analysis) is analysis
    unset = EnginePoolManager(analysis)
    assert unset.ladder_pool is unset.interactive_pool
    assert unset.ladder_config is analysis

    ladder_path = tmp_path / "ladder.toml"
    ladder_path.write_text(
        "[engine]\nname = 'arasan'\nbinary_path = 'arasan'\nthreads = 2\n"
        + ELO_LADDER
        + "movetime_ms = 100\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(ladder_path))
    ladder = ladder_engine_config(analysis)
    assert (ladder.name, ladder.binary_path, ladder.threads) == ("arasan", "arasan", 2)
    assert ladder.ladder.strength == "elo" and ladder.ladder.movetime_ms == 100
    # The analysis engine is untouched by the ladder profile.
    assert (EngineConfig().name, EngineConfig().ladder.strength) == ("caissa", "skill")
    manager = EnginePoolManager.from_env(analysis)
    assert manager.ladder_pool is not manager.interactive_pool
    assert manager.ladder_pool.config is manager.ladder_config
    assert manager.ladder_config.name == "arasan"
    assert manager.interactive_pool.config is analysis


def test_ladder_profile_errors_name_its_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, "ladder.toml")
    with pytest.raises(
        EngineConfigurationError, match=f"{LADDER_ENGINE_CONFIG_ENV} must be an absolute"
    ):
        ladder_engine_profile()
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(tmp_path / "missing.toml"))
    with pytest.raises(EngineConfigurationError, match="does not exist"):
        ladder_engine_profile()


def test_ladder_pool_reserves_cpus_and_one_file_needs_no_second_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analysis_path = tmp_path / "analysis.toml"
    analysis_path.write_text(
        "[engine]\nname = 'caissa'\nbinary_path = 'caissa'\n", encoding="utf-8"
    )
    ladder_path = tmp_path / "ladder.toml"
    ladder_path.write_text(
        "[engine]\nname = 'arasan'\nbinary_path = 'arasan'\nthreads = 2\n", encoding="utf-8"
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(analysis_path))
    monkeypatch.setenv("SCAN64_ENGINE_POOL_INTERACTIVE_CONCURRENCY", "2")
    monkeypatch.delenv("SCAN64_ENGINE_POOL_BATCH_CONCURRENCY", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    monkeypatch.delattr("os.sched_getaffinity", raising=False)
    monkeypatch.delenv(LADDER_ENGINE_CONFIG_ENV, raising=False)
    assert EnginePoolManager.from_env(EngineConfig()).batch_pool.concurrency == 13
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(ladder_path))
    # Two ladder engines of two threads each come out of the batch default.
    assert EnginePoolManager.from_env(EngineConfig()).batch_pool.concurrency == 9
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(analysis_path))
    same = EnginePoolManager.from_env(EngineConfig())
    assert same.ladder_pool is same.interactive_pool
    assert same.batch_pool.concurrency == 13


@pytest.mark.asyncio
async def test_legacy_skill_sessions_stay_on_the_analysis_engine() -> None:
    analysis = EngineConfig(binary_path="stockfish")
    ladder = EngineConfig(binary_path="arasan", name="arasan")
    manager = EnginePoolManager(analysis, 1, 1, ladder_config=ladder)
    used: list[str] = []

    class Lease:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __aenter__(self) -> AsyncMock:
            used.append(self.name)
            adapter = AsyncMock()
            adapter.play_move.return_value = "legacy"
            adapter.play_bot_move.return_value = "ladder"
            return adapter

        async def __aexit__(self, *_: object) -> None:
            return None

    manager.interactive_pool.acquire = lambda: Lease("interactive")  # type: ignore[method-assign]
    manager.ladder_pool.acquire = lambda: Lease("ladder")  # type: ignore[method-assign]
    assert (
        await manager.play_interactive(chess.STARTING_FEN, 5, chess.engine.Limit(time=0.1))
        == "legacy"
    )
    assert await manager.play_bot_interactive(chess.STARTING_FEN, AsyncMock()) == "ladder"
    assert used == ["interactive", "ladder"]


def test_a_ladder_profile_with_its_own_deadline_keeps_its_pool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analysis_path = tmp_path / "analysis.toml"
    analysis_path.write_text(
        "[engine]\nname = 'x'\nbinary_path = 'x'\nsearch_deadline_s = 5\n", encoding="utf-8"
    )
    ladder_path = tmp_path / "ladder.toml"
    ladder_path.write_text(
        "[engine]\nname = 'x'\nbinary_path = 'x'\nsearch_deadline_s = 99\n", encoding="utf-8"
    )
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(analysis_path))
    monkeypatch.setenv(LADDER_ENGINE_CONFIG_ENV, str(ladder_path))
    analysis = EngineConfig()
    ladder = ladder_engine_config(analysis)
    assert ladder is not analysis and ladder.search_deadline_s == 99
