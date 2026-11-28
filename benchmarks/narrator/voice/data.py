# ruff: noqa: E501
# The prompt strings stay verbatim on one line each, as the runs sent them.
"""Data for the narrator voice benchmark (#458): positions, facts, style cards and examples.

Each run of #457 used its own version of the prompt data. The values here are copied byte for
byte from the scripts that produced the stored records in ``results/``; change a run's data only
by adding a new run version (guardrail 6: measurements are immutable).

Positions P1-P5 were used to tune runs 1-8. Positions H1-H5 are held out (run 9).
H2 is illegal; H2b is its legal version (#500).
Every fact about a position is checked with python-chess in ``verify.py``.
"""

# The position before the move each test explains.
FENS = {
    "P1-greedy-knight": "r1b1kbnr/pppp1ppp/8/4N1q1/2BnP3/8/PPPP1PPP/RNBQK2R w KQkq - 1 5",
    "P2-boxed-king": "r1b1kbnr/pppp1Npp/8/8/2Bnq3/8/PPPP1P1P/RNBQKR2 w Qkq - 0 7",
    "P3-back-rank": "6k1/5ppp/8/8/8/8/5PPP/3R2K1 w - - 0 1",
    "P4-fork": "r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1",
    "P5-shut-bishop": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
}

# Runs 1-3: whole comment, facts without roles (run 3 adds ROLES_V1).
SYSTEM_V1 = "You write short chess comments for a club player. Use only the facts given. Do not add any move, square, piece or chess idea that is not in the facts. Write two or three sentences of plain text. No headings and no symbols other than the moves written in the facts."
STYLES_V1 = {
    "Chernev": {
        "traits": "Explains every move to a club player as if sitting beside them. Short, warm sentences. Praises a good move with an exclamation and scolds a lazy one gently. Gives one simple reason for each move. Speaks to the reader: 'Notice how...'.",
        "examples": [
            "White forgets the knight! After 1.Qd2 nothing protects the knight on f3, and Black simply takes it with 1...Bxf3. A modest 1.Be2 would have guarded it.",
            "Notice how the e-file lies open, waiting for a rook. White plays 1.Rb1 and looks the other way, so Black's rook takes the file with 1...Re8. 1.Re1! claims the open file first.",
            "A quiet move, but the wrong one. While White plays 1.a3, Black strikes with 1...d5 and opens the centre, with White's king still sitting there. Castling first, 1.O-O, puts the king in safety.",
        ],
    },
    "Bronstein": {
        "traits": "Writes about the players' ideas and plans more than about variations. Asks what each side wants. Calm, thoughtful, a little ironic. A move is a decision with a purpose and a price; he names both.",
        "examples": [
            "What does White want with 1.Qd2? The price is the knight on f3, which now has no defender, and 1...Bxf3 collects it. 1.Be2 keeps the knight.",
            "Both sides see the open e-file; the question is who gets there first. White's 1.Rb1 answers it in Black's favour: 1...Re8. With 1.Re1 the file would have been White's.",
            "1.a3 is a move without a purpose, and Black's 1...d5 punishes it at once: the centre opens while White's king is still there. The king's safety first: 1.O-O.",
        ],
    },
    "Tal": {
        "traits": "Lively and humorous, full of images: pieces go hunting, kings are left home alone, queens accept invitations. Short punchy sentences and exclamations. Enjoys danger and surprise.",
        "examples": [
            "1.Qd2, and the knight on f3 is suddenly an orphan. Black's bishop takes it home with 1...Bxf3. 1.Be2 would have kept the family together.",
            "The e-file is an open road, and White sends the rook the other way with 1.Rb1! Black's rook is happy to drive in: 1...Re8. 1.Re1 would have taken the road first.",
            "White plays 1.a3, the king stays in the middle, and Black kicks open the door with 1...d5! Before such guests arrive, one castles: 1.O-O.",
        ],
    },
    "Lasker": {
        "traits": "Common sense and principle. Explains a move through a general rule a player can reuse, then shows it on the board. Sober, clear, a little philosophical. No exclamations.",
        "examples": [
            "A piece without a defender is a piece at risk. After 1.Qd2 the knight on f3 stands unprotected, and 1...Bxf3 wins it. 1.Be2 observes the rule and defends it.",
            "An open file belongs to the side whose rook reaches it first. 1.Rb1 neglects this, and Black occupies the e-file with 1...Re8. 1.Re1 was the move of principle.",
            "One should not let the game open while one's king is in the centre. After 1.a3 Black opens it with 1...d5. 1.O-O first secures the king.",
        ],
    },
    "Nimzowitsch": {
        "traits": "Vivid images for strategy: files are roads, undefended pieces are targets, a king in the centre sits on a powder keg. Strong opinions, didactic and witty.",
        "examples": [
            "The knight on f3, abandoned by its comrades after 1.Qd2, is a mere target; 1...Bxf3 harvests it. 1.Be2: protection is the soldier's first duty!",
            "The open e-file is the high road of the rooks. White, with 1.Rb1, wanders off into the side streets, and 1...Re8 seizes the road. 1.Re1 was the necessity.",
            "The king in the centre and the centre about to burst: 1.a3 ignores both dangers. 1...d5 lights the fuse. 1.O-O, and the king is safe from the explosion.",
        ],
    },
    "Capablanca": {
        "traits": "Simple, clear and economical. States the essential point in the fewest words. Calm authority, no flourish.",
        "examples": [
            "1.Qd2 leaves the knight on f3 undefended, and 1...Bxf3 wins it. 1.Be2 was simple and good.",
            "1.Rb1 allows 1...Re8, and Black takes the open e-file. 1.Re1 was correct.",
            "After 1.a3 Black plays 1...d5 and opens the centre while White's king is still there. 1.O-O was necessary.",
        ],
    },
}
EXAMPLE_FACTS_V1 = [
    [
        "White played 1.Qd2.",
        "After 1.Qd2 the knight on f3 is undefended.",
        "Black answers 1...Bxf3: the bishop takes the knight and wins it.",
        "Better was 1.Be2, which defends the knight on f3.",
    ],
    [
        "White played 1.Rb1, moving a rook away from the e-file.",
        "The e-file is open.",
        "Black answers 1...Re8, and Black's rook takes the open e-file.",
        "Better was 1.Re1, which puts White's rook on the open e-file first.",
    ],
    [
        "White played 1.a3.",
        "Black answers 1...d5 and opens the centre.",
        "White's king is still in the centre.",
        "Better was to castle with 1.O-O, which puts the king in safety.",
    ],
]
EXAMPLE_ROLES_V1 = [
    ["The move", "What it misses", "What follows", "The better move"],
    ["The move", "What it misses", "What follows", "The better move"],
    ["The move", "What follows", "What it misses", "The better move"],
]
TESTS_V1 = {
    "P1-greedy-knight": [
        "White played 5.Nxf7.",
        "5.Nxf7 takes the pawn on f7 and attacks the rook on h8.",
        "Black's queen on g5 already attacks the pawn on g2, and 5.Nxf7 leaves it undefended.",
        "Black answers 5...Qxg2, and the queen then attacks the rook on h1.",
        "Better was 5.Bxf7+, which takes the same pawn with check, so Black's king must move.",
    ],
    "P2-boxed-king": [
        "White played 7.Be2 to block the check from the queen on e4.",
        "Black answers 7...Nf3, and it is checkmate.",
        "The bishop on e2 is pinned and cannot take the knight on f3.",
        "Better was 7.Qe2, which also blocks the check and attacks Black's queen on e4.",
    ],
    "P3-back-rank": [
        "White played 1.h3.",
        "Black's king on g8 is shut in by its own pawns on f7, g7 and h7.",
        "Better was 1.Rd8, which is checkmate.",
        "1.h3 only gives White's own king a free square.",
    ],
    "P4-fork": [
        "White played 1.Kd2.",
        "Better was 1.Nc7+, which gives check and also attacks the rook on a8.",
        "After Black's king moves, the knight takes the rook on a8.",
    ],
    "P5-shut-bishop": [
        "White played 1.e3.",
        "After 1.e3 the bishop on c1 still has no free square, because the pawns on b2 and d2 block it.",
        "Better was 1.d4, which puts a pawn in the centre and opens a diagonal for the bishop on c1.",
    ],
}
ROLES_V1 = {
    "P1-greedy-knight": [
        "The move",
        "The move",
        "What it misses",
        "What follows",
        "The better move",
    ],
    "P2-boxed-king": ["The move", "What follows", "Why", "The better move"],
    "P3-back-rank": ["The move", "What it misses", "The better move", "Why the move is weak"],
    "P4-fork": ["The move", "The better move", "What the better move wins"],
    "P5-shut-bishop": ["The move", "What it misses", "The better move"],
}

# Run 4: facts labelled by role, judgements written as facts.
SYSTEM_RUN4 = "You write short chess comments for a club player. Use only the facts given and the position. Do not add any move, square, piece or chess idea that is not in the facts. Do not say that a move costs, forces, allows, risks or prepares anything, and do not call a move necessary, forced, a mistake or a weakness, unless a fact says so. You may retell the facts with lively pictures, but a picture must never add a fact. Write two or three sentences of plain text."
TESTS_RUN4 = {
    "P1-greedy-knight": [
        ("The move", "White played 5.Nxf7. It takes the pawn on f7 and attacks the rook on h8."),
        (
            "What it misses",
            "Black's queen on g5 already attacks the pawn on g2. After 5.Nxf7 nothing defends the pawn on g2.",
        ),
        ("What follows", "Black answers 5...Qxg2. The queen then attacks the rook on h1."),
        (
            "The better move",
            "5.Bxf7+ takes the same pawn with check. Black must answer the check before anything else.",
        ),
    ],
    "P2-boxed-king": [
        ("The move", "White played 7.Be2 to block the check from the queen on e4."),
        ("What follows", "Black answers 7...Nf3, and it is checkmate."),
        (
            "Why",
            "The queen on e4 pins the bishop on e2 to the king, so the bishop cannot take the knight on f3.",
        ),
        (
            "The better move",
            "7.Qe2 also blocks the check, and it attacks Black's queen on e4. So 7.Be2 was not the only way to block the check.",
        ),
    ],
    "P3-back-rank": [
        ("The move", "White played 1.h3. It gives White's own king a free square on h2."),
        ("What it misses", "Black's king on g8 is shut in by its own pawns on f7, g7 and h7."),
        ("The better move", "1.Rd8 is checkmate at once."),
    ],
    "P4-fork": [
        ("The move", "White played 1.Kd2."),
        ("What it misses", "1.Nc7+ gives check and attacks the rook on a8 at the same time."),
        (
            "What the better move wins",
            "Black's king must move, and then the knight takes the rook on a8.",
        ),
    ],
    "P5-shut-bishop": [
        ("The move", "White played 1.e3."),
        (
            "What it misses",
            "The bishop on c1 was already shut in by the pawns on b2 and d2 before 1.e3, and 1.e3 does not free it.",
        ),
        (
            "The better move",
            "1.d4 puts a pawn in the centre and opens a diagonal for the bishop on c1.",
        ),
    ],
}
EXAMPLE_FACTS_RUN4 = [
    [
        ("The move", "White played 1.Qd2."),
        ("What it misses", "After 1.Qd2 the knight on f3 is undefended."),
        ("What follows", "Black answers 1...Bxf3: the bishop takes the knight and wins it."),
        ("The better move", "1.Be2 defends the knight on f3."),
    ],
    [
        ("The move", "White played 1.Rb1, moving a rook away from the e-file."),
        ("What it misses", "The e-file is open."),
        ("What follows", "Black answers 1...Re8, and Black's rook takes the open e-file."),
        ("The better move", "1.Re1 puts White's rook on the open e-file first."),
    ],
    [
        ("The move", "White played 1.a3."),
        ("What it misses", "White's king is still in the centre."),
        ("What follows", "Black answers 1...d5 and opens the centre."),
        ("The better move", "Castling with 1.O-O puts the king in safety first."),
    ],
]
STYLES_RUN4 = {
    "Tal": {
        "traits": "Lively and humorous. Turns each fact into a picture: pieces go hunting, kings dance, queens accept invitations. Short sentences and exclamations. The pictures only retell the facts; they never add a judgement.",
        "examples": [
            "1.Qd2, and the knight on f3 is suddenly an orphan! Black's bishop swoops down with 1...Bxf3 and carries it off. 1.Be2 would have kept the knight under a roof.",
            "The e-file is an open road, and White's rook drives off to b1! Black's rook happily rolls in with 1...Re8. 1.Re1 would have taken the road first.",
            "White plays 1.a3 while the king still sits in the middle, and Black kicks the door open with 1...d5! Castle first with 1.O-O, and the king is safe before the guests arrive.",
        ],
    },
    "Chernev": {
        "traits": "Warm and lively, like a teacher sitting beside the reader. Short sentences, one exclamation for the key moment, and 'Notice how...'. Retells the facts as cause and result; adds no judgement of its own.",
        "examples": [
            "White forgets the knight! After 1.Qd2 the knight on f3 has no friend to protect it, and 1...Bxf3 simply picks it up. A modest 1.Be2 would have guarded it.",
            "Notice the open e-file, an invitation to any rook. White's rook goes to b1 instead, and Black accepts the invitation with 1...Re8. 1.Re1! takes the file first.",
            "White plays 1.a3, but the king still sits in the centre. Black opens it at once with 1...d5! Castling first, 1.O-O, would have put the king in safety.",
        ],
    },
    "Capablanca": {
        "traits": "Simple, clear and economical. States the essential point in the fewest words. Calm, no flourish.",
        "examples": [
            "1.Qd2 leaves the knight on f3 undefended, and 1...Bxf3 wins it. 1.Be2 defends it.",
            "1.Rb1 lets Black's rook take the open e-file with 1...Re8. 1.Re1 takes it first.",
            "After 1.a3 Black opens the centre with 1...d5 while White's king is still there. 1.O-O puts the king in safety first.",
        ],
    },
}

# Run 5: 'White could have played X instead of Y', squares after each move, no '!' rule.
SYSTEM_RUN5 = "You write short chess comments for a club player. Use only the facts given and the position. Do not add any move, square, piece or chess idea that is not in the facts. Do not say that a move costs, forces, allows, risks or prepares anything, and do not call a move necessary, forced, a mistake or a weakness, unless a fact says so. You may retell the facts with lively pictures, but a picture must never add a fact. Write two or three sentences of plain text. Never put '!' after the move White played."
TESTS_RUN5 = {
    "P1-greedy-knight": [
        (
            "The move",
            "White played 5.Nxf7. The knight on f7 has taken the pawn and attacks the rook on h8.",
        ),
        (
            "What it misses",
            "Black's queen on g5 already attacks the pawn on g2. After 5.Nxf7 nothing defends the pawn on g2.",
        ),
        ("What follows", "Black answers 5...Qxg2. The queen on g2 then attacks the rook on h1."),
        (
            "What White could have played",
            "White could have played 5.Bxf7+ instead of 5.Nxf7. The bishop on f7 takes the same pawn with check, and Black must answer the check before anything else.",
        ),
    ],
    "P2-boxed-king": [
        ("The move", "White played 7.Be2 to block the check from the queen on e4."),
        ("What follows", "Black answers 7...Nf3, and it is checkmate."),
        (
            "Why",
            "The queen on e4 pins the bishop on e2 to the king, so the bishop cannot take the knight on f3.",
        ),
        (
            "The better move",
            "7.Qe2 also blocks the check, and it attacks Black's queen on e4. So 7.Be2 was not the only way to block the check.",
        ),
    ],
    "P3-back-rank": [
        ("The move", "White played 1.h3. Now the king on g1 can step to h2."),
        ("What it misses", "Black's king on g8 is shut in by its own pawns on f7, g7 and h7."),
        (
            "What White could have played",
            "White could have played 1.Rd8 instead of 1.h3. The rook on d8 gives checkmate at once.",
        ),
    ],
    "P4-fork": [
        ("The move", "White played 1.Kd2."),
        (
            "What White could have played",
            "White could have played 1.Nc7+ instead of 1.Kd2. The knight on c7 gives check and attacks the rook on a8 at the same time.",
        ),
        (
            "What 1.Nc7+ wins",
            "Black's king must move, and then the knight on c7 takes the rook on a8.",
        ),
    ],
    "P5-shut-bishop": [
        ("The move", "White played 1.e3."),
        (
            "What it misses",
            "The bishop on c1 was already shut in by the pawns on b2 and d2 before 1.e3, and 1.e3 does not free it.",
        ),
        (
            "What White could have played",
            "White could have played 1.d4 instead of 1.e3. The pawn on d4 stands in the centre, and the bishop on c1 can then use the diagonal from c1 to h6.",
        ),
    ],
}
PLAYED = {
    "P1-greedy-knight": "5.Nxf7",
    "P2-boxed-king": "7.Be2",
    "P3-back-rank": "1.h3",
    "P4-fork": "1.Kd2",
    "P5-shut-bishop": "1.e3",
}
EXAMPLE_FACTS_RUN5 = [
    [
        ("The move", "White played 1.Qd2."),
        ("What it misses", "After 1.Qd2 the knight on f3 is undefended."),
        ("What follows", "Black answers 1...Bxf3: the bishop takes the knight on f3 and wins it."),
        (
            "What White could have played",
            "White could have played 1.Be2 instead of 1.Qd2. The bishop on e2 defends the knight on f3.",
        ),
    ],
    [
        ("The move", "White played 1.Rb1, moving a rook away from the e-file."),
        ("What it misses", "The e-file is open."),
        ("What follows", "Black answers 1...Re8, and Black's rook on e8 takes the open e-file."),
        (
            "What White could have played",
            "White could have played 1.Re1 instead of 1.Rb1. The rook on e1 takes the open e-file first.",
        ),
    ],
    [
        ("The move", "White played 1.a3."),
        ("What it misses", "White's king is still in the centre."),
        ("What follows", "Black answers 1...d5 and opens the centre."),
        (
            "What White could have played",
            "White could have castled with 1.O-O instead of 1.a3. The king is then in safety first.",
        ),
    ],
]
EXAMPLES_RUN5 = {
    "Tal": [
        "1.Qd2, and the knight on f3 is suddenly an orphan. Black's bishop swoops down with 1...Bxf3 and carries it off! 1.Be2 would have kept the knight under a roof.",
        "The e-file is an open road, and White's rook drives off to b1. Black's rook happily rolls in with 1...Re8! White could have taken the road first with 1.Re1.",
        "White plays 1.a3 while the king still sits in the middle, and Black kicks the door open with 1...d5! Castling first, 1.O-O, puts the king in safety before the guests arrive.",
    ],
    "Chernev": [
        "White forgets the knight. After 1.Qd2 the knight on f3 has no friend to protect it, and 1...Bxf3 simply picks it up! A modest 1.Be2 would have guarded it.",
        "Notice the open e-file, an invitation to any rook. White's rook goes to b1 instead, and Black accepts the invitation with 1...Re8. White could have taken the file first with 1.Re1!",
        "White plays 1.a3, but the king still sits in the centre. Black opens it at once with 1...d5! Castling first, 1.O-O, would have put the king in safety.",
    ],
    "Capablanca": STYLES_RUN4["Capablanca"]["examples"],
}

# Runs 6-9: one fact per call. Each example sentence rewrites one fact of EXAMPLE_FACTS_RUN5.
SYSTEM_RUN6 = "You rewrite one chess fact as one lively sentence for a club player. Keep every move, square and piece of the fact, and add none. Do not add any judgement, plan or danger the fact does not state. Never put '!' after the move White played. Write plain text, one sentence."
CARDS_RUN6 = {
    "Tal": "Lively and humorous. Turns the fact into a picture: pieces go hunting, kings dance, queens accept invitations. Short, with an exclamation now and then. The picture only retells the fact.",
    "Chernev": "Warm and lively, like a teacher sitting beside the reader. Short and clear, sometimes 'Notice that...'. Retells the fact as cause and result.",
}
SENTENCES_RUN6 = {
    "Tal": [
        [
            "1.Qd2, a quiet queen step.",
            "But look: the knight on f3 is left an orphan, undefended!",
            "Black's bishop swoops down with 1...Bxf3 and carries the knight off!",
            "1.Be2 instead would have kept the knight on f3 under a roof, with the bishop on e2 beside it.",
        ],
        [
            "White's rook drives off to b1, away from the e-file.",
            "Yet the e-file is an open road!",
            "Black's rook happily rolls in with 1...Re8, and the rook on e8 takes the open e-file.",
            "1.Re1 instead would have put White's rook on e1, on that road first!",
        ],
        [
            "White plays a quiet 1.a3.",
            "But White's king still sits in the middle of the house, in the centre!",
            "Black kicks the door open with 1...d5 and opens the centre!",
            "Castling first with 1.O-O instead of 1.a3 would have put the king in safety.",
        ],
    ],
    "Chernev": [
        [
            "White plays 1.Qd2.",
            "Notice that after 1.Qd2 the knight on f3 is undefended.",
            "Black simply answers 1...Bxf3, and the bishop takes the knight on f3 and wins it.",
            "White could have played 1.Be2 instead of 1.Qd2, and the bishop on e2 would have defended the knight on f3.",
        ],
        [
            "White moves a rook to b1 with 1.Rb1, away from the e-file.",
            "Notice that the e-file is open.",
            "Black answers 1...Re8, and the rook on e8 takes the open e-file!",
            "White could have played 1.Re1 instead of 1.Rb1, taking the open e-file first with the rook on e1.",
        ],
        [
            "White plays 1.a3.",
            "Notice that White's king is still in the centre.",
            "Black answers at once with 1...d5 and opens the centre!",
            "White could have castled with 1.O-O instead of 1.a3, putting the king in safety first.",
        ],
    ],
}

# Run 7 only: the comment so far as context, and livelier Chernev sentences.
SYSTEM_RUN7 = "You rewrite one chess fact as one lively sentence for a club player. Keep every move, square and piece of the fact, and add none. Do not add any judgement, plan or danger the fact does not state. Never put '!' after the move White played. Write plain text, one sentence. Continue the comment so far naturally, and do not repeat its words."
SENTENCES_CHERNEV_RUN7 = [
    [
        "White plays 1.Qd2, and the queen steps up to d2.",
        "But notice: after 1.Qd2 the knight on f3 has been left undefended!",
        "Black pounces at once with 1...Bxf3, and the bishop walks off with the knight on f3!",
        "How much better was 1.Be2 instead of 1.Qd2, when the bishop on e2 stands guard over the knight on f3!",
    ],
    [
        "White's rook wanders over to b1 with 1.Rb1, turning its back on the e-file.",
        "Notice that the e-file lies wide open, waiting for a rook!",
        "Black accepts the invitation with 1...Re8, and the rook on e8 makes the open e-file its own.",
        "White could have played 1.Re1 instead of 1.Rb1 and claimed the open e-file first!",
    ],
    [
        "White plays the quiet 1.a3.",
        "But notice where White's king is: still in the centre!",
        "Black strikes at once with 1...d5 and tears the centre open!",
        "Castling with 1.O-O instead of 1.a3 would have tucked the king away in safety first.",
    ],
]

# Run 8: words that never count as colour words in the repetition check.
STOP_RUN8 = [
    "a",
    "after",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "before",
    "black",
    "black's",
    "but",
    "by",
    "can",
    "could",
    "first",
    "for",
    "from",
    "had",
    "has",
    "have",
    "here",
    "in",
    "instead",
    "is",
    "it",
    "its",
    "just",
    "move",
    "moves",
    "no",
    "not",
    "now",
    "of",
    "on",
    "one",
    "or",
    "play",
    "played",
    "plays",
    "so",
    "some",
    "still",
    "than",
    "that",
    "the",
    "then",
    "there",
    "this",
    "to",
    "too",
    "two",
    "very",
    "was",
    "were",
    "while",
    "white",
    "white's",
    "will",
    "with",
    "would",
]

# Run 9: held-out positions and the optional 'own words' rule.
OWN_WORDS_RULE = " Use your own words; never copy the fact's wording."
FENS_HELDOUT = {
    "H1-missed-mate": "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4",
    "H2-skewer": "8/1r6/8/3k4/8/8/6B1/6K1 w - - 0 1",
    "H3-stalemate": "7k/8/6K1/8/8/8/8/5Q2 w - - 0 1",
    "H4-pin": "4k3/8/8/4n3/8/8/5P2/4R1K1 w - - 0 1",
    "H5-allowed-fork": "6k1/8/8/8/3n4/8/6PP/3Q2K1 w - - 0 1",
}
TESTS_HELDOUT = {
    "H1-missed-mate": [
        ("The move", "White played 4.d3. The pawn on d3 defends the bishop on c4."),
        ("What it misses", "Black's knight on f6 attacks White's queen on h5."),
        (
            "What White could have played",
            "White could have played 4.Qxf7# instead of 4.d3. The queen takes the pawn on f7 and gives checkmate.",
        ),
    ],
    "H2-skewer": [
        ("The move", "White played 1.Kf2."),
        (
            "What White could have played",
            "White could have played 1.Bf3+ instead of 1.Kf2. The bishop on f3 gives check to the king on d5.",
        ),
        (
            "What 1.Bf3+ wins",
            "Black's king must step off the diagonal, and then the bishop on f3 can take the rook on b7.",
        ),
    ],
    "H3-stalemate": [
        (
            "The move",
            "White played 1.Qf7. Black's king on h8 is not in check and has no move, so it is stalemate.",
        ),
        ("What follows", "The game ends in a draw."),
        (
            "What White could have played",
            "White could have played 1.Qf8# instead of 1.Qf7. The queen on f8 gives checkmate.",
        ),
    ],
    "H4-pin": [
        ("The move", "White played 1.Kg2."),
        (
            "What it misses",
            "Black's knight on e5 cannot move, because the rook on e1 pins it to the king on e8.",
        ),
        (
            "What White could have played",
            "White could have played 1.f4 instead of 1.Kg2. The pawn on f4 attacks the pinned knight on e5, and Black cannot save the knight.",
        ),
    ],
    "H5-allowed-fork": [
        ("The move", "White played 1.Qc1."),
        (
            "What follows",
            "Black answers 1...Ne2+. The knight on e2 gives check to the king on g1 and attacks the queen on c1.",
        ),
        (
            "What follows next",
            "White's king must move, and then the knight on e2 can take the queen on c1.",
        ),
        (
            "What White could have played",
            "White could have played 1.Qxd4 instead of 1.Qc1. The queen takes the knight on d4.",
        ),
    ],
}
PLAYED_HELDOUT = {
    "H1-missed-mate": "4.d3",
    "H2-skewer": "1.Kf2",
    "H3-stalemate": "1.Qf7",
    "H4-pin": "1.Kg2",
    "H5-allowed-fork": "1.Qc1",
}

# H2-skewer above is illegal: the bishop on g2 already checks the king, with White to move. The
# run 9 records came from it, so it stays as it is (guardrail 6). H2b-skewer is the legal version,
# with the bishop on e2 and the same moves and facts. No stored run uses it: a new
# run version adds it.
FENS_CORRECTED = {"H2b-skewer": "8/1r6/8/3k4/8/8/4B3/6K1 w - - 0 1"}
TESTS_CORRECTED = {"H2b-skewer": TESTS_HELDOUT["H2-skewer"]}
PLAYED_CORRECTED = {"H2b-skewer": PLAYED_HELDOUT["H2-skewer"]}
ILLEGAL_POSITIONS = {"H2-skewer"}
