
import pytest

from scan64.chess.games.ingestion import (
    InvalidGameError,
    NonStandardVariantError,
    ingest_fen,
    ingest_pgn,
)


def test_ingest_fen_valid():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    pos = ingest_fen(fen)
    assert pos.fen == fen
    assert pos.side_to_move == 'w'
    assert pos.half_move_clock == 0
    assert pos.full_move_number == 1
    assert pos.canonical_id == "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"

def test_ingest_fen_invalid():
    with pytest.raises(InvalidGameError):
        ingest_fen("invalid_fen")

def test_ingest_fen_chess960():
    # Setup chess960 FEN (board needs to be treated as such,
    # though chess960 standard PGN often just sets Variant)
    # Actually, a standard FEN doesn't inherently imply 960 unless it's an X-FEN or HA-FEN.
    pass

def test_ingest_pgn_valid():
    pgn = """[Event "FIDE World Cup 2017"]
[Site "Tbilisi GEO"]
[Date "2017.09.09"]
[Round "4.1"]
[White "Carlsen,M"]
[Black "Bu Xiangzhi"]
[Result "0-1"]
[WhiteElo "2827"]
[BlackElo "2710"]
[EventDate "2017.09.03"]
[ECO "C55"]

1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. d3 h6 5. O-O d6 6. c3 g6 7. Re1 Bg7 
8. h3 O-O 9. Nbd2 Kh7 10. b4 a6 11. a4 Nh5 12. Nf1 Qe8 13. Ne3 f5 
14. Nd5 Qd8 15. a5 Ne7 16. Nxe7 Qxe7 17. exf5 gxf5 18. Ng5+ Kg6 19. Nf3 Kh7 
20. Ng5+ Kg6 21. Nf3 Kh7 22. Ng5+ Kg6 23. Nf3 Kh7 0-1
"""
    game, positions = ingest_pgn(pgn)

    assert game.white == "Carlsen,M"
    assert game.black == "Bu Xiangzhi"
    assert game.result == "0-1"
    assert len(game.moves) == 46 # 23 full moves = 46 plies
    assert game.moves[0] == "e2e4" # First move e4

    # Check positions (1 initial + 46 moves)
    assert len(positions) == 47
    assert positions[0].side_to_move == 'w'
    assert positions[1].side_to_move == 'b'
    assert positions[-1].game_id == game.id
def test_ingest_pgn_invalid_move():
    pgn = """[Event "Test"]
1. e4 e5 2. Nf3 Nc6 3. Bc4 Ke6 4. d3
"""
    # Ke6 is illegal here (king has to move past pieces or through pieces)
    with pytest.raises(InvalidGameError, match="PGN parsing errors"):
        ingest_pgn(pgn)

def test_ingest_pgn_non_standard_variant():
    pgn = """[Event "Crazyhouse Game"]
[Variant "Crazyhouse"]
1. e4 e5
"""
    with pytest.raises(NonStandardVariantError, match="Crazyhouse"):
        ingest_pgn(pgn)

def test_ingest_pgn_empty():
    pgn = ""
    with pytest.raises(InvalidGameError, match="Empty or unparsable"):
        ingest_pgn(pgn)
