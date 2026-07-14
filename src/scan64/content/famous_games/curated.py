from __future__ import annotations

from scan64.content.famous_games.models import (
    AssetItem,
    FamousGameDecision,
    FamousGameDefinition,
    FamousGamePayload,
    VerifiedAlternative,
)

_SCORE_SOURCE = "https://archive.org/details/morphysgamesofch00morpiala"
_STUDY_SOURCE = "https://github.com/Mathews-Tom/Scan64"
_SCORE_IDENTIFIER = "morphysgamesofch00morpiala"


def _moves(score: str) -> list[str]:
    return score.split()


def _assets(game_id: str) -> list[AssetItem]:
    return [
        AssetItem(
            asset_type="game_score",
            source_url=_SCORE_SOURCE,
            licence="Public Domain",
            content_identifier=f"{_SCORE_IDENTIFIER}:{game_id}",
        ),
        AssetItem(
            asset_type="study_material",
            source_url=_STUDY_SOURCE,
            licence="GPL-3.0-or-later",
            content_identifier=f"scan64-famous-game-study:{game_id}",
        ),
    ]


FAMOUS_GAMES: list[FamousGameDefinition] = [
    FamousGameDefinition(
        id="morphy-opera-1858",
        payload=FamousGamePayload(
            title="Morphy vs Duke Karl and Count Isouard, Paris 1858",
            historical_context=(
                "Paul Morphy played this informal opera-house game during his 1858 European tour."
            ),
            strategic_context=(
                "Rapid development and open lines make every tempo against the king decisive."
            ),
            moves=_moves(
                "e4 e5 Nf3 d6 d4 Bg4 dxe5 Bxf3 Qxf3 dxe5 Bc4 Nf6 Qb3 Qe7 Nc3 c6 "
                "Bg5 b5 Nxb5 cxb5 Bxb5+ Nbd7 O-O-O Rd8 Rxd7 Rxd7 Rd1 Qe6 Bxd7+ Nxd7 "
                "Qb8+ Nxb8 Rd8+"
            ),
            decisions=[
                FamousGameDecision(
                    id="opera-open-lines",
                    ply=18,
                    fen="rn2kb1r/p3qppp/2p2n2/1p2p1B1/2B1P3/1QN5/PPP2PPP/R3K2R w KQkq - 0 10",
                    prompt="Which sacrifice opens decisive lines against Black's uncastled king?",
                    played_move="Nxb5",
                    accepted_moves=["Nxb5"],
                    verified_alternatives=[
                        VerifiedAlternative(
                            san="Bxf6",
                            explanation=(
                                "It damages Black's kingside but does not force the same "
                                "immediate line opening."
                            ),
                        )
                    ],
                    hints=[
                        "Look at the c6 pawn that shields Black's queenside.",
                        "A knight capture can remove that pawn with tempo.",
                    ],
                    comparison=(
                        "Nxb5! clears b5 and c6, letting the bishops and queen attack before Black "
                        "can coordinate."
                    ),
                )
            ],
        ),
        assets=_assets("morphy-opera-1858"),
        skill_mapping={"tactics.sacrifice": 1.0, "tactics.development": 1.0},
        difficulty_estimate=1450.0,
    ),
    FamousGameDefinition(
        id="morphy-paulsen-1857",
        payload=FamousGamePayload(
            title="Morphy vs Paulsen, First American Chess Congress 1857",
            historical_context=(
                "Morphy met Louis Paulsen at the First American Chess Congress in New York."
            ),
            strategic_context=(
                "A space gain only matters when the pieces can exploit the squares it controls."
            ),
            moves=_moves(
                "e4 c5 d4 cxd4 Nf3 e6 Nxd4 Bc5 Nb3 Bb6 Nc3 Ne7 Bf4 O-O Bd6 f5 e5 a6 "
                "Be2 Nbc6 O-O Rf7 Kh1 f4 Ne4 Nf5 Bh5 g6 Bg4 Ng7 Qf3 h5 Bh3 Qh4 Nf6+ Kh8 "
                "Qe4 Qg5 g3 f3 Nd2 Bd8 Nxf3 Qh6 Rg1 Bxf6 exf6 Ne8 Bf4 Nxf6 Qxc6 Qxf4 "
                "Qxc8+ Rxc8 gxf4 Rxc2 Rac1 Rxf2 Rc8+ Ng8 Ne5 Rg7 Nxg6+ Kh7 Nf8+ Kh6 "
                "Nxd7 Rxd7 Rcxg8 Rxf4 Bxe6 Re7"
            ),
            decisions=[
                FamousGameDecision(
                    id="paulsen-outpost",
                    ply=14,
                    fen="rnbq1rk1/pp1pnppp/1b2p3/8/4PB2/1NN5/PPP2PPP/R2QKB1R w KQ - 7 8",
                    prompt="Which move places a developed piece on a durable, active square?",
                    played_move="Bd6",
                    accepted_moves=["Bd6"],
                    verified_alternatives=[
                        VerifiedAlternative(
                            san="Nc5",
                            explanation=(
                                "The knight jump is active, but it gives Black more freedom "
                                "to challenge the bishop pair."
                            ),
                        )
                    ],
                    hints=[
                        "Compare the bishop's targets from d6 with its current square.",
                        "The move should also make Black's pawn advance less comfortable.",
                    ],
                    comparison=(
                        "Bd6 fixes the bishop on an outpost and increases pressure "
                        "before Black can complete development."
                    ),
                )
            ],
        ),
        assets=_assets("morphy-paulsen-1857"),
        skill_mapping={"positional.outpost": 1.0, "positional.space": 1.0},
        difficulty_estimate=1600.0,
    ),
    FamousGameDefinition(
        id="morphy-barnes-c41-1858",
        payload=FamousGamePayload(
            title="Morphy vs Barnes, London 1858",
            historical_context=(
                "Morphy faced Thomas Wilson Barnes during his London visit in 1858."
            ),
            strategic_context=(
                "Development remains urgent when the center has opened and the opponent's king is "
                "still central."
            ),
            moves=_moves(
                "e4 e5 Nf3 d6 d4 exd4 Bc4 Be7 c3 d3 Qb3 Be6 Bxe6 fxe6 Qxb7 Nd7 Qb5 Nf6 "
                "Ng5 Rb8 Qa4 O-O Nxe6 Nc5 Nxc5 dxc5 Qc4+ Kh8 O-O Ng4 f4 d2 Bxd2 Rxb2 "
                "a3 Rxd2 Nxd2 Ne3 Qe2 Nxf1 Rxf1 Qd7 Nc4 Qb5 e5 Bh4 f5 Be7 Qg4 Qd7 Rd1 "
                "Qxf5 Qxf5 Rxf5 Rd7 Bf8 e6"
            ),
            decisions=[
                FamousGameDecision(
                    id="barnes-development",
                    ply=6,
                    fen="rnbqkbnr/ppp2ppp/3p4/8/3pP3/5N2/PPP2PPP/RNBQKB1R w KQkq - 0 4",
                    prompt="Which move develops with tempo toward Black's uncastled king?",
                    played_move="Bc4",
                    accepted_moves=["Bc4"],
                    verified_alternatives=[
                        VerifiedAlternative(
                            san="Nxd4",
                            explanation=(
                                "Recapturing the pawn is sound but does not accelerate "
                                "development as directly."
                            ),
                        )
                    ],
                    hints=[
                        "Choose a move that develops a piece while eyeing the vulnerable f7 "
                        "square.",
                        "The king has not castled and the center is already open.",
                    ],
                    comparison=(
                        "Bc4 develops with pressure on f7, forcing Black to account for a tactical "
                        "target immediately."
                    ),
                )
            ],
        ),
        assets=_assets("morphy-barnes-c41-1858"),
        skill_mapping={"tactics.development": 1.0, "tactics.king_safety": 1.0},
        difficulty_estimate=1500.0,
    ),
    FamousGameDefinition(
        id="morphy-barnes-c42-1858",
        payload=FamousGamePayload(
            title="Morphy vs Barnes, London 1858: Attacking the Center",
            historical_context=(
                "This second London game against Barnes shows Morphy converting activity into a "
                "direct attack."
            ),
            strategic_context=(
                "When an advanced enemy piece can be challenged, develop while increasing the "
                "pressure on it."
            ),
            moves=_moves(
                "e4 e5 Bc4 Nf6 Nf3 Nxe4 Nc3 Nxc3 dxc3 f6 O-O Nc6 Nh4 Qe7 Nf5 Qc5 Bb3 d5 "
                "Be3 Qa5 Nh4 Be6 Qh5+ g6 Nxg6 Bf7 Qh4 Bxg6 Qxf6 Rg8 Rad1 Be7 Qe6 Bf7 "
                "Qh3 Nd8 f4 e4 Rxd5 Bxd5 Qh5+ Kf8 Bxd5 Rg7 b4 Qa6 f5 Nf7 f6 Bxf6 b5 Qd6 "
                "Bxf7 b6 Bh6 Ke7 Bxg7 Bxg7 Bb3 Rf8 Rf7+ Rxf7 Qxf7+ Kd8 Qxg7 Qd1+ Kf2 "
                "Qd2+ Kg3 e3 Qf6+ Kc8 Be6+ Kb7 Qf3+"
            ),
            decisions=[
                FamousGameDecision(
                    id="barnes-challenge-center",
                    ply=6,
                    fen="rnbqkb1r/pppp1ppp/8/4p3/2B1n3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4",
                    prompt="Which developing move attacks Black's central knight?",
                    played_move="Nc3",
                    accepted_moves=["Nc3"],
                    verified_alternatives=[
                        VerifiedAlternative(
                            san="Bxf7+",
                            explanation=(
                                "The check is legal, but it commits material before completing "
                                "development."
                            ),
                        )
                    ],
                    hints=[
                        "A queenside knight can attack e4 while joining the center.",
                        "Prefer a developing threat to an immediate sacrifice.",
                    ],
                    comparison=(
                        "Nc3 attacks e4 and prepares the recapture sequence while bringing another "
                        "piece into play."
                    ),
                )
            ],
        ),
        assets=_assets("morphy-barnes-c42-1858"),
        skill_mapping={"tactics.development": 1.0, "tactics.center_control": 1.0},
        difficulty_estimate=1550.0,
    ),
    FamousGameDefinition(
        id="morphy-anderssen-1858",
        payload=FamousGamePayload(
            title="Morphy vs Anderssen, Paris 1858",
            historical_context=(
                "Morphy and Adolf Anderssen played their celebrated Paris match in 1858."
            ),
            strategic_context=(
                "An attack succeeds when development creates more threats than the defender can "
                "answer."
            ),
            moves=_moves(
                "e4 e5 f4 exf4 Nf3 g5 Bc4 Bg7 O-O d6 c3 Nc6 Qb3 Qe7 d4 Nf6 Nxg5 Nxe4 "
                "Bxf7+ Kd8 Nxe4 Qxe4 Bxf4 Bh3 gxh3 Nxd4 Nd2 Ne2+ Kf2 Qxf4+ Kxe2 Qg5 "
                "Rae1 Bh6 Qd5 Re8+ Kd1"
            ),
            decisions=[
                FamousGameDecision(
                    id="anderssen-pressure-f7",
                    ply=12,
                    fen="r1bqk1nr/ppp2pbp/2np4/6p1/2B1Pp2/2P2N2/PP1P2PP/RNBQ1RK1 w kq - 1 7",
                    prompt=(
                        "Which queen move increases pressure on f7 while keeping the attack "
                        "coordinated?"
                    ),
                    played_move="Qb3",
                    accepted_moves=["Qb3"],
                    verified_alternatives=[
                        VerifiedAlternative(
                            san="d4",
                            explanation=(
                                "The central advance is principled but allows Black more "
                                "time to meet threats around f7."
                            ),
                        )
                    ],
                    hints=[
                        "The bishop on c4 already attacks f7.",
                        "Place the queen where it adds a second attacker to f7.",
                    ],
                    comparison=(
                        "Qb3 creates a direct double attack on f7 and b7, increasing the burden on "
                        "Black's undeveloped pieces."
                    ),
                )
            ],
        ),
        assets=_assets("morphy-anderssen-1858"),
        skill_mapping={"tactics.king_attack": 1.0, "tactics.development": 1.0},
        difficulty_estimate=1650.0,
    ),
]
