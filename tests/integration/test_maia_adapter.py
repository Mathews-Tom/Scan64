from pathlib import Path

import pytest

from scan64.chess.opponents.protocols import OpponentContext
from scan64.chess.positions.models import Position
from scan64.providers.maia import (
    MaiaCheckpoint,
    MaiaConfig,
    MaiaConfigurationError,
    MaiaOpponentProvider,
)


@pytest.mark.asyncio
async def test_maia_adapter_fails_closed_before_engine_start_for_missing_weights(
    tmp_path: Path,
) -> None:
    binary_path = tmp_path / "lc0"
    binary_path.touch()
    provider = MaiaOpponentProvider(
        MaiaConfig(
            binary_path=binary_path,
            checkpoints=(
                MaiaCheckpoint(
                    rating=1500,
                    weights_path=tmp_path / "missing-maia-1500.pb.gz",
                ),
            ),
        )
    )
    position = Position(
        fen="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        side_to_move="w",
        canonical_id="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
    )

    with pytest.raises(MaiaConfigurationError, match="Maia checkpoint does not exist"):
        await provider.choose_move(position, OpponentContext(strength_setting=1500))
