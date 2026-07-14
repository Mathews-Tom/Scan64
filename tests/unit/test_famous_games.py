import chess
import pytest

from scan64.content.famous_games.curated import FAMOUS_GAMES
from scan64.content.famous_games.models import (
    AssetItem,
    FamousGameDecision,
    FamousGameDefinition,
    FamousGamePayload,
    VerifiedAlternative,
)
from scan64.content.validate import validate_famous_game, validate_famous_games


def make_definition() -> FamousGameDefinition:
    return FamousGameDefinition(
        id="opera-game",
        payload=FamousGamePayload(
            title="Opera Game",
            historical_context="A public-domain 1858 game score.",
            strategic_context="Rapid development enables a direct attack.",
            moves=["e4", "e5"] * 12,
            decisions=[
                FamousGameDecision(
                    id="develop",
                    ply=2,
                    fen="rnbqkbnr/pppppppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
                    prompt="Develop a piece.",
                    played_move="Nf3",
                    accepted_moves=["Nf3"],
                    verified_alternatives=[
                        VerifiedAlternative(san="Bc4", explanation="It also develops a piece.")
                    ],
                    hints=["Improve a kingside minor piece."],
                    comparison="Nf3 attacks e5 while preparing castling.",
                )
            ],
        ),
        assets=[
            AssetItem(
                asset_type="game_score",
                source_url="https://example.com/score",
                licence="Public Domain",
                content_identifier="score-1",
            ),
            AssetItem(
                asset_type="study_material",
                source_url="https://example.com/study",
                licence="CC BY-SA 4.0",
                content_identifier="study-1",
            ),
        ],
        skill_mapping={"development": 1.0},
    )


def test_famous_game_definition_builds_canonical_content_item() -> None:
    item = make_definition().to_content_item()

    assert item.id == "opera-game"
    assert item.domain == "famous_games"
    assert item.licence == "Public Domain"
    assert item.payload["decisions"][0]["played_move"] == "Nf3"
    assert item.payload["assets"][1]["asset_type"] == "study_material"


def test_famous_game_definition_rejects_missing_asset_category() -> None:
    definition = make_definition()

    with pytest.raises(ValueError, match="score and study material"):
        definition.model_copy(update={"assets": definition.assets[:1]}).validate_assets()


def test_curated_famous_games_are_complete_and_legal() -> None:
    assert validate_famous_games()
    assert len(FAMOUS_GAMES) == 5
    assert all(len(game.payload.moves) >= 24 for game in FAMOUS_GAMES)
    assert all(game.payload.decisions for game in FAMOUS_GAMES)
    assert all(
        score.source_url == "https://archive.org/details/morphysgamesofch00morpiala"
        and score.content_identifier.startswith("morphysgamesofch00morpiala:")
        for game in FAMOUS_GAMES
        for score in game.assets
        if score.asset_type == "game_score"
    )


def test_famous_game_validator_rejects_mismatched_decision_position() -> None:
    game = FAMOUS_GAMES[0]
    decision = game.payload.decisions[0].model_copy(update={"fen": chess.STARTING_FEN})
    payload = game.payload.model_copy(update={"decisions": [decision]})
    invalid_game = game.model_copy(update={"payload": payload})

    assert validate_famous_game(invalid_game, 0) == (
        "game 0 decision opera-open-lines FEN does not match score"
    )
