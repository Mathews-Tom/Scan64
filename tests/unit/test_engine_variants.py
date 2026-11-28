"""Which variants an engine declares, in each protocol's names (#398)."""

from __future__ import annotations

import asyncio

import chess
import chess.engine
import chess.variant
import pytest

from scan64.providers.engine.uci import EngineCapabilityError, require_variant


def _xboard(variants: str | None) -> chess.engine.XBoardProtocol:
    async def build() -> chess.engine.XBoardProtocol:
        engine = chess.engine.XBoardProtocol()
        engine.id["name"] = "FakeXBoard"
        if variants is not None:
            engine.features["variants"] = variants
        return engine

    return asyncio.run(build())


def test_xboard_names_antichess_giveaway() -> None:
    engine = _xboard("normal,giveaway,crazyhouse,3check,atomic")
    for board in (
        chess.variant.AntichessBoard(),
        chess.variant.CrazyhouseBoard(),
        chess.variant.ThreeCheckBoard(),
        chess.Board(),
    ):
        require_variant(engine, board)


@pytest.mark.parametrize("variants", [None, "normal,crazyhouse"])
def test_xboard_refuses_an_undeclared_variant(variants: str | None) -> None:
    with pytest.raises(EngineCapabilityError, match="Analysing antichess needs an engine"):
        require_variant(_xboard(variants), chess.variant.AntichessBoard())


def test_xboard_without_a_variants_feature_plays_standard_chess() -> None:
    require_variant(_xboard(None), chess.Board())
