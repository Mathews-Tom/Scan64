"""Relational chess claims in free prose, licensed by the facts (#431).

The vocabulary rule (#408, ``slots.chess_words_outside_slots``) checks single
chess words. A claim such as "You are up a piece" or "your king is safe" holds
no chess word and no notation, so it passed unchecked. This module reads such
claims with a closed table of patterns (``CLAIM_RULES``) and checks each one,
with its side and sign, against the facts of the cited evidence item
(``ClaimFacts``):

- material balance ("you are up a piece", "you are a pawn down", "material is
  level"): a fact position with that learner material advantage;
- material change ("you won the exchange", "this loses a pawn"): a fact line
  whose material change from the root is that;
- evaluation ("you are winning", "the opponent is better", "the position is
  equal"): an engine score of a fact position past the threshold;
- king safety ("your king is safe", "the opponent's king is exposed"): a
  passed King factor change with that sign;
- threats ("the opponent has no counterplay", "you have the initiative"): a
  passed Threats factor change with that sign;
- piece activity ("your pieces are passive", "you have more space"): a passed
  Pieces, Mobility or Space factor change with that sign.

Side words: ``you``/``your``/``the learner``/``the side to move`` are the
learner (the side to move in the grounding FEN); ``your opponent``/``the
opponent``/``they``/``their`` the other side. A claim with no side word holds
when either side has the fact. The table is closed: a claim no rule reads is
left to the vocabulary rule, which rejects its chess words. The check only
adds rejections: a supported claim still passes the vocabulary rule as well.

A claim is checked against every fact position of the item (before the move,
after each move of its lines), not one moment: "you were winning" and "you are
winning" are the same claim.

Analysis Board lines (#449): the words that state a line's material outcome
(``OUTCOME_WORDS``: material, win, lose, gain, equal, advantage, ahead, behind)
are no fact name, so the vocabulary rule rejects them. On an Analysis Board
evidence item, ``outcome_claims`` admits them inside a material claim that the
line's outcome facts (``OutcomeFacts``: the material change over the whole line
and the balance at its end) support, side and size checked, and returns the
first such claim they do not support. A line item has its own line; the
``comparison`` item every line, and its claim must hold for each. A side-free
claim is about the side to move only when the side to move is its subject just
before it ("the side to move ends up winning material") or it ends "for the
side to move"; any other subject is read as no side. A claim in a sentence
with a negation ("does not", "never", "neither", "without") is rejected as
negated. Outside its claims, the sentence may hold only slots and the words of
``OUTCOME_SENTENCE_WORDS``; any other word leaves the outcome words to the
vocabulary rule. Lessons have no outcome facts.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import cache

from scan64.explanations.factor_facts import FactorChange
from scan64.explanations.factors import PARTS
from scan64.explanations.facts import (
    CentipawnEvaluation,
    MateEvaluation,
    Material,
    MoveFacts,
    UnknownEvaluation,
    evaluation_as_cp,
)

# A slot as a material count ("you are {eval_diff} pawns up in material"):
# the slot states an evaluation, not material, so no material fact has that size.
SLOT_AMOUNT = -1

# Engine-score thresholds, centipawns, learner's view.
BETTER_CP = 50  # "better", "an advantage", "the edge"
WINNING_CP = 200  # "winning", "a decisive advantage", "lost"
EQUAL_CP = 50  # "equal", "balanced", "level"

# Factor groups behind each factor topic.
FACTOR_TOPICS = {
    "King": "king",
    "Threats": "threats",
    "Pieces": "activity",
    "Mobility": "activity",
    "Space": "activity",
}
_GROUP_OF_PART = dict(PARTS)


def factor_group(change: FactorChange) -> str:
    """The evaluation group of a factor change: its own name, or its part's group."""
    return (
        change.guide_name if change.kind == "group" else _GROUP_OF_PART.get(change.guide_name, "")
    )


@dataclass(frozen=True)
class ClaimFacts:
    """Every relational fact a claim citing one evidence item may state, learner's view."""

    balances: frozenset[int] = frozenset()  # learner material advantage, per fact position
    changes: frozenset[int] = frozenset()  # learner material change from the root, per ply
    evaluations: frozenset[int] = frozenset()  # engine scores, centipawns (mates as large)
    # (topic, sign of the change, reference point: "before" or "best").
    factors: frozenset[tuple[str, int, str]] = frozenset()

    @classmethod
    def collect(
        cls,
        root: Material,
        evaluations: Iterable[CentipawnEvaluation | MateEvaluation | UnknownEvaluation],
        lines: Iterable[Sequence[MoveFacts]],
        factors: Iterable[FactorChange] = (),
    ) -> ClaimFacts:
        """Facts of a root position, its scores, the move lines from it and factor changes."""
        balances = {root.learner_advantage}
        changes: set[int] = set()
        scores = {cp for e in evaluations if (cp := evaluation_as_cp(e)) is not None}
        for line in lines:
            for move in line:
                advantage = move.material_after.learner_advantage
                balances.add(advantage)
                changes.add(advantage - root.learner_advantage)
                if (cp := evaluation_as_cp(move.evaluation_after)) is not None:
                    scores.add(cp)
        topics = set()
        for change in factors:
            group = factor_group(change)
            if group in FACTOR_TOPICS and change.change_pawns != 0:
                sign = 1 if change.change_pawns > 0 else -1
                topics.add((FACTOR_TOPICS[group], sign, change.compared_with))
        return cls(frozenset(balances), frozenset(changes), frozenset(scores), frozenset(topics))


# --- Claim patterns ------------------------------------------------------------------

_LEARNER = r"you|the\s+learner|the\s+side\s+to\s+move"
_OPPONENT = r"your\s+opponent|the\s+opponent|they|the\s+other\s+side"
_SUBJECT = rf"(?P<who>{_OPPONENT}|{_LEARNER})"
_OWNER = (
    r"(?P<owner>your\s+opponent's|the\s+opponent's|opponent's|their|the\s+learner's"
    r"|the\s+side\s+to\s+move's|your|the)"
)
# A form of "to be", with contractions, a negation (``neg``) and a few adverbs.
_BE = (
    r"(?:'re|'s|\s+(?:(?P<neg>aren't|isn't|wasn't|weren't)|is|are|was|were|will\s+be"
    r"|would\s+be|stays?|remains?|stayed|remained|ends?\s+up|ended\s+up|has\s+been"
    r"|have\s+been|look|looks|looked|stand|stands|stood))"
    r"(?:\s+(?P<neg2>not|never|no\s+longer))?"
    r"(?:\s+(?:now|still|already|clearly|slightly|much|far|well|just|completely|totally"
    r"|simply|probably|definitely|a\s+bit|quite|very|more|less|so|left))*"
)
_HAVE = (
    r"(?:'ve|\s+(?:have|has|had|get|gets|got|keep|keeps|kept|hold|holds|held|retain|retains"
    r"|retained|gain|gains|gained|enjoy|enjoys|keep|still\s+have|still\s+has|now\s+have"
    r"|now\s+has))"
)
# "~" is a slot used as the count ("{eval_diff} pawns up"): ``SLOT_AMOUNT``.
_COUNT = (
    r"(?:(?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|the|~)\s+)?"
    r"(?:(?:full|whole|clean|extra|entire|additional)\s+)?"
)
_UNIT = (
    r"(?P<unit>minor\s+pieces?|pieces?|pawns?|knights?|bishops?|rooks?|queens?|exchange"
    r"|points?(?:\s+of\s+material)?|material)"
)
_UP = r"(?P<dir>up|down|ahead|behind)"
# A required count: "a pawn", "two pawns", "the exchange"; bare "pawns" is no claim
# ("minor pieces behind pawns" is a factor name).
_COUNT_REQUIRED = (
    r"(?P<count>a|an|one|two|three|four|five|six|seven|eight|nine|the|~)\s+"
    r"(?:(?:full|whole|clean|extra|entire|additional)\s+)?"
)
_UNIT_PIECES = (
    r"(?P<unit>minor\s+pieces?|pieces?|pawns?|knights?|bishops?|rooks?|queens?|exchange"
    r"|points?(?:\s+of\s+material)?)"
)
_NUMBERS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "the": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}
_UNIT_VALUES = {
    "pawn": 1,
    "point": 1,
    "exchange": 2,
    "piece": 3,
    "minor piece": 3,
    "knight": 3,
    "bishop": 3,
    "rook": 5,
    "queen": 9,
}
_GAIN = r"win|wins|won|winning|gain|gains|gained|gaining|net|nets|netted|netting"
_GAIN += r"|picks?\s+up|picked\s+up|picking\s+up|grab|grabs|grabbed|grabbing"
_LOSS = r"lose|loses|lost|losing|drop|drops|dropped|dropping|give\s+up|gives\s+up|gave\s+up"
_LOSS += r"|giving\s+up|sacrifice|sacrifices|sacrificed|sacrificing"
_LOSS += r"|blunder|blunders|blundered|blundering|cost|costs|costing"
_KING_GOOD = r"safe|safer|secure|sheltered|well\s+protected|protected|solid|fine|out\s+of\s+danger"
_KING_BAD = (
    r"exposed|weak|weaker|weakened|vulnerable|unsafe|open|airy|naked|bare|insecure"
    r"|unprotected|in\s+danger|under\s+attack|under\s+fire|under\s+pressure|in\s+trouble"
)
_UP_WORDS = (
    r"improved|improves|rose|rises|grew|grows|increased|increases|got\s+better|gets\s+better"
    r"|strengthened"
)
_DOWN_WORDS = (
    r"worsened|worsens|suffered|suffers|fell|falls|dropped|drops|decreased|decreases|declined"
    r"|deteriorated|got\s+worse|gets\s+worse|weakened"
)
_THREATS = (
    r"counterplay|counter-play|threats?|the\s+initiative|initiative|attacking\s+chances"
    r"|compensation|an\s+attack|the\s+attack|a\s+strong\s+attack"
)
_FEW = r"no|little|hardly\s+any|not\s+much|not\s+any|zero|no\s+real|nothing\s+like"
_ACTIVE = (
    r"active|more\s+active|well\s+placed|better\s+placed|well\s+coordinated|coordinated"
    r"|developed|well\s+developed|better\s+developed|dominant|centralised|centralized"
)
_PASSIVE = (
    r"passive|inactive|cramped|tied\s+up|badly\s+placed|poorly\s+placed|misplaced|stuck"
    r"|undeveloped|uncoordinated|restricted|boxed\s+in|out\s+of\s+play"
)
_ACTIVITY = r"space|mobility|activity|piece\s+activity|development|piece\s+placement"
# Plain tone ("you are struggling with this", "you are in control of your
# learning") is no claim, so only position words are read.
_BETTER = r"better|ahead"
_WORSE = r"worse|behind|in\s+(?:serious\s+|real\s+|big\s+|deep\s+)?(?:trouble|danger)"
# Words after an evaluation word that make it tone: "ahead of the curve",
# "behind on the clock", "winning the learning game", "better than this".
_NOT_A_POSITION = (
    r"(?!\s+(?:off|than|at|of|on|the|a|an|this|that|your|to|with|prepared|for\s+it)\b)"
)
_GOOD = (
    r"better|stronger|good|great|excellent|fine|solid|safe|safer|intact|healthy|improved"
    r"|high|superior|secure"
)
_BAD = (
    r"worse|weaker|poor|bad|weak|broken|damaged|shattered|low|inferior|compromised|ruined"
    r"|lacking|exposed|vulnerable"
)
_POSITION_GOOD = r"winning|won|better|good|great|strong|excellent|comfortable|pleasant|healthy"
_POSITION_BAD = (
    r"losing|lost|worse|bad|weak|difficult|terrible|poor|critical|dangerous|hopeless"
    r"|uncomfortable|tough|awful"
)
_WINNING = r"winning|won|completely\s+winning|clearly\s+winning|totally\s+winning"
_LOSING = r"losing|lost|completely\s+lost|clearly\s+lost|totally\s+lost"
_EQUAL = r"equal|balanced|level|even|drawn|drawish|roughly\s+equal|about\s+equal"
_ADVANTAGE_BIG = r"decisive|winning|overwhelming|crushing|huge|big|large|clear|serious|lasting"
_ADVANTAGE_SMALL = r"small|slight|tiny|positional|nice|real|pleasant"


@dataclass(frozen=True)
class Claim:
    """One claim read from prose: its topic, the side it is about and its sign.

    ``side`` is ``1`` for the learner, ``-1`` for the opponent, ``0`` when the
    prose names neither. ``sign`` is from that side's view: ``1`` up, ``-1``
    down, ``0`` equal. ``amount`` is material in points, or ``None`` for any.
    ``level`` is the engine-score threshold of an evaluation claim.
    """

    topic: str
    side: int
    sign: int
    amount: int | None = None
    level: int = 0


def _side(who: str | None) -> int:
    if who is None:
        return 0
    who = " ".join(who.lower().split())
    if who == "the":
        return 0
    if "opponent" in who or who in ("they", "them", "their", "the other side"):
        return -1
    return 1


_Reader = Callable[[re.Match[str]], Claim]


def _amount(match: re.Match[str]) -> int | None:
    """Points of material the match names; ``None`` for bare "material"."""
    groups = match.groupdict()
    if "~" in (groups.get("count"), groups.get("count2")):
        return SLOT_AMOUNT
    raw = groups.get("unit")
    if raw is None:
        return None
    unit = " ".join(raw.lower().split()).removesuffix(" of material")
    if unit == "material":
        return None
    unit = unit.removesuffix("s") if unit != "exchange" else unit
    count = _NUMBERS[(match.groupdict().get("count") or "a").lower()]
    return count * _UNIT_VALUES[unit]


def _balance(sign: int) -> _Reader:
    def read(match: re.Match[str]) -> Claim:
        direction = match.groupdict().get("dir")
        if direction is not None:
            claim_sign = 1 if direction.lower() in ("up", "ahead") else -1
        else:
            claim_sign = sign
        groups = match.groupdict()
        # The side is a subject ("you are up a pawn") or an owner ("your material edge").
        side = _side(groups.get("who") or groups.get("owner"))
        return Claim("balance", side, claim_sign, _amount(match))

    return read


def _change(match: re.Match[str]) -> Claim:
    verb = " ".join(match.group("verb").lower().split())
    sign = 1 if re.fullmatch(_GAIN, verb) else -1
    owner = _side(match.group("owner"))
    if owner != 0:  # "you win their knight", "wins your rook": the owner loses it.
        return Claim("change", owner, -1, _amount(match))
    if match.group("obj") is not None:  # "costs you a pawn"
        return Claim("change", _side(match.group("obj")), -1, _amount(match))
    return Claim("change", _side(match.group("who")), sign, _amount(match))


def _signed(topic: str, *, level: int = 0, sign: int | None = None) -> _Reader:
    """A claim of ``topic``; with ``sign`` unset, a ``minus`` group match means -1."""

    def read(match: re.Match[str]) -> Claim:
        groups = match.groupdict()
        side = _side(groups.get("who") or groups.get("owner"))
        claim_sign = sign if sign is not None else (-1 if groups.get("minus") else 1)
        return Claim(topic, side, claim_sign, level=level)

    return read


def _advantage(match: re.Match[str]) -> Claim:
    size = (match.group("size") or "").lower()
    level = WINNING_CP if size in _ADVANTAGE_BIG.split("|") else BETTER_CP
    sign = -1 if match.group("minus") else 1
    return Claim("evaluation", _side(match.group("who")), sign, level=level)


def _factor_direction(match: re.Match[str]) -> Claim:
    topic = match.group("topic").lower()
    if topic.startswith("king") or "shelter" in topic:
        kind = "king"
    else:
        kind = "threats" if "threat" in topic else "activity"
    return Claim(kind, _side(match.group("owner")), -1 if match.group("minus") else 1)


_I = re.IGNORECASE
_FACTOR_TOPIC = (
    r"king\s+safety|king\s+shelter|pawn\s+shelter|threats|mobility|space|piece\s+placement"
    r"|piece\s+activity|activity|development"
)
# A unit with a count ("a piece", "the exchange") or bare "material".
_COUNTED = rf"(?:{_COUNT_REQUIRED}{_UNIT_PIECES}|(?P<unit_m>material))"
_POSITION_BE = (
    r"\b(?:the\s+)?(?:position|game|evaluation|score)(?:\s+(?:is|was|stays|remains|looks"
    r"|stayed|remained|seems))(?:\s+(?:now|still|roughly|about|dead|completely|clearly))*\s+"
)


@dataclass(frozen=True)
class _Rule:
    """One claim pattern, compiled on first use: importing the module stays cheap."""

    source: str
    flags: int = 0

    def compiled(self) -> re.Pattern[str]:
        return _compile(self.source, self.flags)


@cache
def _compile(source: str, flags: int) -> re.Pattern[str]:
    return re.compile(source, flags)


# Rules run in order; each match is checked, then blanked so a later rule does
# not read it again.
CLAIM_RULES: tuple[tuple[_Rule, _Reader], ...] = (
    # Material change: "you won the exchange", "this loses a pawn", "costs you a piece".
    (
        _Rule(
            rf"(?:\b{_SUBJECT}(?:\s+(?:will|would|can|could|have|has|had|just|then|now|also"
            r"|might|may|soon))*\s+)?"
            rf"\b(?P<verb>{_GAIN}|{_LOSS})\s+(?:back\s+)?"
            r"(?:(?P<obj>you|your\s+opponent|the\s+opponent|them)\s+)?"
            rf"{_COUNT}(?:{_OWNER}\s+)?{_UNIT}\b",
            _I,
        ),
        _change,
    ),
    # Material balance: "material is level", "you are up a piece", "you're a pawn down".
    (
        _Rule(
            r"\bmaterial(?:\s+balance)?(?:\s+(?:that|which))?"
            r"(?:\s+at\s+the\s+end(?:\s+of\s+(?:this|the)\s+line)?)?"
            r"(?:\s+(?:is|was|stays|remains|stayed|remained|being))"
            rf"(?:\s+(?:now|still|roughly|about))*\s+(?:{_EQUAL})\b"
            rf"|\b(?:{_EQUAL})\s+material\b",
            _I,
        ),
        lambda match: Claim("balance", 0, 0, 0),
    ),
    # No material change (#449): "the material balance does not change".
    (
        _Rule(
            r"\bmaterial(?:\s+balance)?(?:\s+(?:that|which))?\s+(?:does\s+not|doesn't|did\s+not"
            r"|didn't|will\s+not|won't|never)\s+changes?\b"
            r"|\bno\s+change\s+(?:to|in)\s+the\s+material(?:\s+balance)?\b"
            r"|\bmaterial(?:\s+balance)?\s+not\s+changing\b|\bno\s+material\s+change\b"
            r"|\bmaterial(?:\s+balance)?(?:\s+(?:that|which))?"
            r"(?:\s+(?:is|was|stays|remains|stayed|remained|being))\s+(?:the\s+same|unchanged)\b",
            _I,
        ),
        lambda match: Claim("change", 0, 0, 0),
    ),
    # Owner subjects: "your material is up two pawns", "your side is two pawns ahead".
    (
        _Rule(
            rf"\b{_OWNER}\s+(?:material|side|army|forces|position){_BE}\s+{_UP}\s+(?:by\s+)?"
            rf"(?:in\s+|on\s+)?{_COUNTED}\b",
            _I,
        ),
        _balance(1),
    ),
    (
        _Rule(
            rf"\b{_OWNER}\s+(?:material|side|army|forces|position){_BE}\s+{_COUNT_REQUIRED}"
            rf"{_UNIT_PIECES}\s+{_UP}\b",
            _I,
        ),
        _balance(1),
    ),
    # "You lead by two pawns", "you have two pawns more", "your extra pawn".
    (
        _Rule(rf"\b{_SUBJECT}\s+(?:lead|leads|led)\s+by\s+{_COUNT_REQUIRED}{_UNIT_PIECES}", _I),
        _balance(1),
    ),
    # "You are two pawns to the good", "two pawns to the good".
    (
        _Rule(
            rf"(?:\b{_SUBJECT}{_BE}\s+)?(?:\b|(?=~)){_COUNT_REQUIRED}{_UNIT_PIECES}\s+to\s+the\s+good\b",
            _I,
        ),
        _balance(1),
    ),
    # "A material edge of two pawns (for you)".
    (
        _Rule(
            r"\bmaterial\s+(?:edge|lead|advantage|plus)\s+of\s+"
            rf"{_COUNT_REQUIRED}{_UNIT_PIECES}(?:\s+for\s+(?P<who>{_OPPONENT}|them|{_LEARNER}))?",
            _I,
        ),
        _balance(1),
    ),
    (
        _Rule(
            rf"(?:\b{_SUBJECT}{_HAVE}\s+)?(?:\b|(?=~)){_COUNT_REQUIRED}{_UNIT_PIECES}\s+"
            r"(?:more|(?P<dir>fewer|less))\b",
            _I,
        ),
        lambda match: Claim(
            "balance",
            _side(match.group("who")),
            -1 if match.group("dir") else 1,
            _amount(match),
        ),
    ),
    (
        # Singular only: "your extra pieces are active" names no count.
        _Rule(
            rf"\b{_OWNER}\s+(?:extra|spare|additional)\s+(?P<unit>pawn|piece|minor\s+piece"
            r"|knight|bishop|rook|queen|exchange)\b",
            _I,
        ),
        _balance(1),
    ),
    # "a two-pawn lead", "your two-pawn edge".
    (
        _Rule(
            rf"(?:\b{_SUBJECT}{_HAVE}\s+(?:a|an|the)\s+|\b{_OWNER}\s+|\b(?:a|an)\s+)"
            r"(?P<count>one|two|three|four|five|six|seven|eight|nine)[-\s](?P<unit>pawn|piece|knight"
            r"|bishop|rook|queen|point)\s+(?:material\s+)?(?:edge|lead|advantage|plus)\b",
            _I,
        ),
        _balance(1),
    ),
    (
        _Rule(
            rf"(?:\b{_SUBJECT}{_BE}\s+)?(?:\b|(?=~)){_COUNT_REQUIRED}{_UNIT_PIECES}\s+{_UP}\b"
            r"|(?:\b|(?=~))(?P<count2>~)\s+pawns?\s+(?:of|in)\s+material\b",
            _I,
        ),
        _balance(1),
    ),
    (
        _Rule(
            # Not "the evaluation went down by {eval_diff} pawns" (a score moving).
            r"(?<!went\s)(?<!goes\s)(?<!gone\s)(?<!go\s)(?<!came\s)(?<!comes\s)"
            rf"(?:\b{_SUBJECT}{_BE}\s+)?\b{_UP}\s+(?:by\s+)?(?:in\s+|on\s+)?{_COUNTED}\b",
            _I,
        ),
        _balance(1),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:{_COUNT_REQUIRED})?(?:extra|additional|spare|more)"
            rf"\s+{_UNIT}\b",
            _I,
        ),
        _balance(1),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:a\s+lot\s+|much\s+|far\s+)?(?:more|(?P<minus>less|fewer))"
            r"\s+(?:material|pieces)\b",
            _I,
        ),
        _signed("balance"),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:a|the)\s+material\s+(?:advantage|edge|lead|plus"
            r"|(?P<minus>deficit|disadvantage))\b",
            _I,
        ),
        _signed("balance"),
    ),
    # "a material advantage for the side to move", "a material deficit for the opponent" (#449).
    (
        _Rule(
            r"\bmaterial\s+(?:advantage|edge|lead|plus|(?P<minus>deficit|disadvantage))"
            rf"\s+for\s+(?P<who>{_OPPONENT}|them|{_LEARNER})\b",
            _I,
        ),
        _signed("balance"),
    ),
    (
        _Rule(
            rf"\b{_OWNER}\s+(?:material\s+(?:edge|lead|advantage|plus|balance)|edge|lead"
            r"|advantage|plus)(?:\s+in\s+material)?\s+(?:is|was)"
            rf"\s+{_COUNT_REQUIRED}{_UNIT_PIECES}",
            _I,
        ),
        _balance(1),
    ),
    # Evaluation: "you are winning", "the opponent is better", "the position is equal".
    (
        _Rule(rf"\b{_SUBJECT}{_BE}\s+(?:{_WINNING}|(?P<minus>{_LOSING}))\b{_NOT_A_POSITION}", _I),
        _signed("evaluation", level=WINNING_CP),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_BE}\s+(?:{_BETTER}|(?P<minus>{_WORSE}))\b{_NOT_A_POSITION}",
            _I,
        ),
        _signed("evaluation", level=BETTER_CP),
    ),
    (
        _Rule(
            rf"{_POSITION_BE}(?:{_EQUAL})\b|\b(?:an?\s+)?(?:{_EQUAL})\s+(?:position|game"
            r"|ending|endgame)\b",
            _I,
        ),
        lambda match: Claim("evaluation", 0, 0, level=EQUAL_CP),
    ),
    (
        _Rule(
            rf"{_POSITION_BE}(?:{_WINNING}|(?P<minus>{_LOSING}))(?:\s+for\s+{_SUBJECT})?\b",
            _I,
        ),
        _signed("evaluation", level=WINNING_CP),
    ),
    (
        _Rule(
            rf"\b{_OWNER}\s+(?:position|game){_BE}\s+(?:(?P<big>winning|won)|{_POSITION_GOOD}"
            rf"|(?P<minus>(?P<big2>losing|lost)|{_POSITION_BAD}))\b"
            # "worse for you in terms of {factor:...}" compares a factor, not the score.
            r"(?!(?:\s+for\s+you)?\s+(?:than|in\s+terms\s+of)\b)",
            _I,
        ),
        lambda match: Claim(
            "evaluation",
            _side(match.group("owner")),
            -1 if match.group("minus") else 1,
            level=WINNING_CP if match.group("big") or match.group("big2") else BETTER_CP,
        ),
    ),
    (
        _Rule(
            r"\bfavou?r(?:s|ed|ing)?\s+(?P<who>your\s+opponent|the\s+opponent|them|you"
            r"|the\s+side\s+to\s+move)\b",
            _I,
        ),
        _signed("evaluation", level=BETTER_CP),
    ),
    (
        _Rule(rf"\b{_SUBJECT}{_BE}\s+materially\s+{_UP}\b", _I),
        _balance(1),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:a|an|the)\s+(?:(?P<size>{_ADVANTAGE_BIG}"
            rf"|{_ADVANTAGE_SMALL})\s+)?(?:(?P<minus>disadvantage)|advantage|edge|upper\s+hand"
            r"|lead|plus|better\s+(?:position|game))\b",
            _I,
        ),
        _advantage,
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:a|an|the)\s+(?:winning|won|(?P<minus>losing|lost))"
            r"\s+(?:position|game)\b",
            _I,
        ),
        _signed("evaluation", level=WINNING_CP),
    ),
    # King safety: "your king is safe", "the opponent's king is exposed".
    (
        _Rule(
            rf"\b{_OWNER}\s+king(?:'s)?(?:\s+(?:position|shelter|cover|safety))?"
            rf"(?:{_BE})?(?:\s+(?:became|becomes|become|got|gets|feels|felt|seems"
            r"|has\s+become))?(?:\s+(?:now|still|very|more|less|quite|completely|so|too"
            r"|in\s+immediate|in\s+real|in\s+serious|in\s+any|in\s+great))*"
            rf"\s+(?:{_KING_GOOD}|(?P<minus>{_KING_BAD}|danger|trouble))\b",
            _I,
        ),
        _signed("king"),
    ),
    # A factor topic with a direction: "your king safety suffered", "your mobility improved".
    (
        _Rule(
            rf"(?:\b{_OWNER}\s+)?\b(?P<topic>{_FACTOR_TOPIC})"
            rf"(?:\s+(?:has|have|had|really|also|clearly))*\s+(?:{_UP_WORDS}"
            rf"|(?P<minus>{_DOWN_WORDS}))\b",
            _I,
        ),
        _factor_direction,
    ),
    # A factor topic with a judgement: "your king safety is poor", "... is stronger".
    (
        _Rule(
            rf"(?:\b{_OWNER}\s+)?\b(?P<topic>{_FACTOR_TOPIC}){_BE}\s+(?:{_GOOD}"
            rf"|(?P<minus>{_BAD}))\b",
            _I,
        ),
        _factor_direction,
    ),
    # Threats: "the opponent has no counterplay", "you have the initiative".
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:(?P<minus>{_FEW})\s+)?(?:(?:real|serious|strong"
            r"|dangerous|some|many|lots\s+of|a\s+lot\s+of|good|nasty)\s+)?"
            rf"(?:{_THREATS})\b(?!\s+to\b)",
            _I,
        ),
        _signed("threats"),
    ),
    (
        _Rule(rf"\bthere(?:'s|\s+(?:is|are|was|were))\s+(?:{_FEW})\s+(?:{_THREATS})\b", _I),
        _signed("threats", sign=-1),
    ),
    # Piece activity: "your pieces are passive", "you have more space".
    (
        _Rule(
            rf"\b{_OWNER}\s+(?:pieces|army|forces){_BE}\s+(?:{_ACTIVE}|better|stronger"
            rf"|(?P<minus>{_PASSIVE}|worse|weaker))\b",
            _I,
        ),
        _signed("activity"),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_BE}\s+(?:more\s+active|(?P<minus>passive|cramped|tied\s+up))\b",
            _I,
        ),
        _signed("activity"),
    ),
    (
        _Rule(
            rf"\b{_SUBJECT}{_HAVE}\s+(?:a\s+lot\s+|much\s+|far\s+)?(?:more|better|extra"
            rf"|(?P<minus>less|worse|no|little|poor))\s+(?:{_ACTIVITY})\b",
            _I,
        ),
        _signed("activity"),
    ),
    # Bare threat words name a threats fact: "no counterplay". ("Initiative" is
    # also tone: "that shows real initiative".)
    (
        _Rule(r"\b(?:counterplay|counter-play|compensation)\b", _I),
        lambda match: Claim("threats", 0, 0),
    ),
)


def supported(claim: Claim, facts: ClaimFacts, *, about_best: bool = False) -> bool:
    """Whether some fact of ``facts`` states ``claim`` (side and sign checked).

    ``about_best``: the claim is about the best move ("the best move keeps
    your king safer", "your king is safe only after the best move"). A
    factor change against the best move is the played move minus the best
    move, so such a king, threats or activity claim needs a change against
    the best move with the opposite sign.
    """
    signs = (1, -1) if claim.side == 0 else (claim.side,)
    if claim.topic in ("balance", "change"):
        values = facts.balances if claim.topic == "balance" else facts.changes
        if claim.sign == 0:
            return 0 in values
        for side in signs:
            target = side * claim.sign
            for value in values:
                gained = target * value
                if claim.amount == SLOT_AMOUNT:
                    return False
                if claim.amount is None:
                    if gained > 0:
                        return True
                # "about a minor piece": a piece and a pawn still counts as a piece.
                elif claim.amount <= gained <= claim.amount + (1 if claim.amount >= 3 else 0):
                    return True
        return False
    if claim.topic == "evaluation":
        if claim.sign == 0:
            return any(abs(score) <= claim.level for score in facts.evaluations)
        return any(
            side * claim.sign * score >= claim.level
            for side in signs
            for score in facts.evaluations
        )
    if claim.sign == 0:
        return any(topic == claim.topic for topic, _, _ in facts.factors)
    if about_best:
        return any((claim.topic, -side * claim.sign, "best") in facts.factors for side in signs)
    return any(
        (claim.topic, side * claim.sign, reference) in facts.factors
        for side in signs
        for reference in ("before", "best")
    )


@dataclass(frozen=True)
class RejectedClaim:
    """A claim the validator rejects: its text, and whether it is negated."""

    text: str
    negated: bool


# The best move named in the same sentence as a claim: before it as the
# subject ("the best move keeps your king safer") or after it as a condition
# ("your king is safe only after the best move"), not as the base of a
# comparison ("worse than after the best move"). The validator writes the
# {best} slot as "the best move" in the prose it passes here.
_BEST_SUBJECT = re.compile(
    r"\b(?:the\s+)?(?:best|better|right|stronger|correct)\s+(?:move|one|choice|option|reply)\b",
    re.IGNORECASE,
)
_BEST_CONDITION = re.compile(
    r"^(?:(?!\bthan\b)[^.!?;])*?\b(?:with|after|by|only\s+with|only\s+after)\s+(?:the\s+)?(?:best|better|right"
    r"|stronger|correct)\s+(?:move|one|choice|option|reply)\b",
    re.IGNORECASE,
)


def _about_best(prose: str, match: re.Match[str]) -> bool:
    sentence_start = max(prose.rfind(mark, 0, match.start()) for mark in ".!?;") + 1
    before = prose[sentence_start : match.start()]
    return bool(_BEST_SUBJECT.search(before) or _BEST_CONDITION.search(prose[match.end() :]))


# A slot as the pawn amount ("~ pawns") in a sentence with material wording:
# the slot states an evaluation, and the wording states it as material
# ("you are {eval_diff} pawns, up in material", "your material edge is
# {eval_diff} pawns", "you have {eval_diff} more pawns").
_SLOT_PAWNS = re.compile(r"~\s+(?:(?:more|extra|fewer|additional|spare)\s+)?pawns?\b", re.I)
_MATERIAL_WORDING = re.compile(
    r"\b(?:material|materially|ahead|behind|in\s+front|richer|poorer|to\s+the\s+good|extra"
    r"|spare|additional|lead|leads|leading|short|plus|captur\w*|win|wins|won|winning"
    r"|gain|gains|gained|gaining|lose|loses|lost|losing)\b"
    r"|~\s+(?:more|fewer)\s+pawns?|\b(?:have|has|had)\s+~\s+pawns?",
    re.I,
)
# A sentence about the engine score, with no material word: its slot amount
# is an evaluation ("the evaluation is down {eval} pawns", "costs {eval}
# pawns of evaluation"), so the slot-as-material checks do not apply.
_EVALUATION_WORDING = re.compile(
    r"\b(?:evaluation|eval|score|scores|scored|swing|rating|rated|assessment|difference"
    r"|value)\b",
    re.I,
)
_SENTENCE_END = re.compile(r"[.!?;]")


# Material words that keep a score sentence a material claim ("score: you
# capture {eval_diff} pawns", "the evaluation shows you up {eval_diff} pawns
# of pieces", "evaluation: you have {eval} pawns more").
_MATERIAL_IN_SCORE = re.compile(
    r"\b(?:material\w*|captur\w*|win|wins|won|winning|gain\w*|lose|loses|lost|losing"
    r"|pieces?|have|has|had)\b",
    re.I,
)


def _evaluation_sentence(sentence: str) -> bool:
    return bool(_EVALUATION_WORDING.search(sentence)) and not _MATERIAL_IN_SCORE.search(sentence)


def _sentence_at(prose: str, index: int) -> str:
    start = max(prose.rfind(mark, 0, index) for mark in ".!?;") + 1
    end = _SENTENCE_END.search(prose, index)
    return prose[start : end.start() if end else len(prose)]


def _slot_as_material(prose: str) -> str | None:
    for sentence in _SENTENCE_END.split(prose):
        if (
            _SLOT_PAWNS.search(sentence)
            and _MATERIAL_WORDING.search(sentence)
            and not _evaluation_sentence(sentence)
        ):
            return sentence.strip()
    return None


def unsupported_claim(prose: str, facts: ClaimFacts) -> RejectedClaim | None:
    """The first claim in ``prose`` that is negated or no fact supports, else ``None``.

    The validator runs it after the vocabulary rule, and it only adds
    rejections: a supported claim must also pass the vocabulary rule, so its
    chess words ("won", "exchange") must be fact names too. Within this pass
    a matched claim is blanked so a later rule does not read it again.
    """
    if (sentence := _slot_as_material(prose)) is not None:
        return RejectedClaim(sentence, negated=False)
    remaining = prose
    for rule, read in CLAIM_RULES:
        pattern = rule.compiled()
        for match in pattern.finditer(remaining):
            groups = match.groupdict()
            # A negated claim states an absence, which no fact records.
            if groups.get("neg") or groups.get("neg2"):
                return RejectedClaim(match[0].strip(), negated=True)
            claim = read(match)
            if claim.amount == SLOT_AMOUNT and _evaluation_sentence(
                _sentence_at(prose, match.start())
            ):
                continue  # "the evaluation is down {eval} pawns": a score, not material
            if not supported(claim, facts, about_best=_about_best(prose, match)):
                return RejectedClaim(match[0].strip(), negated=False)
        remaining = pattern.sub(lambda match: " " * len(match[0]), remaining)
    return None


# --- Line outcome words (#449) ---------------------------------------------------------

# The material words of ``slots.CHESS_WORDS`` that state a line's outcome.
OUTCOME_WORDS = re.compile(
    r"\b(?:material|win|wins|winning|won|lose|loses|losing|lost|gain|gains|gained|gaining"
    r"|ahead|behind|advantage|equal)\b",
    re.IGNORECASE,
)
# "wins material for the opponent": the side the claim is about, after it.
_FOR_SIDE = re.compile(rf"\s+for\s+(?P<who>{_OPPONENT}|them|{_LEARNER})\b", re.IGNORECASE)
# A side-free claim right after the side to move as its subject, across a few
# linking words: "the side to move ends up winning material", "... being up a
# point of material". Any other subject ("the other player", "he", a move
# slot) is not read as either side.
_LEARNER_BEFORE = re.compile(
    rf"\b(?:{_LEARNER})(?:\s+(?:is|was|being|ends?\s+up|ended\s+up|ending\s+up|gets?|got"
    r"|getting|then|also|still|now|just|has|have|had|will|would|can|could|keeps?|kept"
    r"|keeping|stays?|stayed|remains?|remained))*\s*$",
    re.IGNORECASE,
)
# A negation anywhere in the sentence outside the claim: "the side to move does
# not win material", "neither side wins material", "... and line 1 does not".
# The pattern rules read no such form, so the claim is not read as either sign.
_NEGATION = re.compile(
    r"\b(?:not|no|never|neither|nor|none|nobody|nothing|without|cannot|fails?|failed"
    r"|failing|hardly|barely)\b|n't\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OutcomeFacts:
    """A line's material outcome, side to move's view (Analysis Board, #449).

    ``change`` is the material the side to move wins over the whole line
    (negative: loses); ``balance`` its material advantage at the line's end.
    """

    change: int
    balance: int


# The only words an outcome claim's sentence may hold outside its claims, the
# side to move as their subject and a "for <side>" after them (second pre-PR
# review): sentence frame, line and slot talk. Any other word (a size such as
# "by nine points", "so does the opponent", "only in theory", "the opponent of
# the side to move") leaves the outcome words to the vocabulary rule.
OUTCOME_SENTENCE_WORDS = frozenset(
    """a an the this that it its in over at by with after of to and so then also here
    line lines line's first second third top main primary engine engine's choice option
    path suggestion move sequence continuation start starts started begin begins began
    show shows showing indicate indicates indicating mean means result results resulting
    lead leads leading end ends ending ended up where which as is are was be being has
    have all both overall throughout whole course keep keeps kept keeping maintain
    maintains maintained leave leaves while balance""".split()
)
_SENTENCE_WORD = re.compile(r"~|[a-z']+", re.IGNORECASE)


def _sentence_span(prose: str, start: int, end: int) -> tuple[int, int]:
    first = max(prose.rfind(mark, 0, start) for mark in ".!?;") + 1
    end_mark = _SENTENCE_END.search(prose, end)
    return first, end_mark.start() if end_mark else len(prose)


def _learner_before(prose: str, match: re.Match[str]) -> re.Match[str] | None:
    first, _ = _sentence_span(prose, match.start(), match.end())
    return _LEARNER_BEFORE.search(prose, first, match.start())


def _outcome_side(claim: Claim, prose: str, match: re.Match[str]) -> int | None:
    """The side an outcome claim is about: its own side word, a following "for ...",
    or the side to move as the subject just before it; else ``None`` (no side read).

    A "for ..." that names the other side than the claim's own is no side either.
    """
    after = _FOR_SIDE.match(prose, match.end())
    if claim.side != 0:
        if after is not None and _side(after.group("who")) != claim.side:
            return None
        return claim.side
    if after is not None:
        return _side(after.group("who"))
    if _learner_before(prose, match) is not None:
        return 1
    return None


def _plain_sentence(prose: str, match: re.Match[str], claims: Sequence[tuple[int, int]]) -> bool:
    """Whether the sentence of ``match`` holds only ``OUTCOME_SENTENCE_WORDS`` and slots
    outside its outcome claims, the side to move before ``match`` and a "for <side>" after."""
    first, last = _sentence_span(prose, match.start(), match.end())
    cut = [(begin, end) for begin, end in claims if first <= begin and end <= last]
    if (after := _FOR_SIDE.match(prose, match.end())) is not None:
        cut.append(after.span())
    if (before := _learner_before(prose, match)) is not None:
        cut.append(before.span())
    chars = list(prose[first:last])
    for begin, end in cut:
        chars[begin - first : end - first] = " " * (end - begin)
    rest = "".join(chars)
    return all(
        word.lower() in OUTCOME_SENTENCE_WORDS or word == "~"
        for word in _SENTENCE_WORD.findall(rest)
    )


def _negated_around(prose: str, match: re.Match[str]) -> bool:
    """Whether the sentence of ``match`` holds a negation outside the match itself."""
    start = max(prose.rfind(mark, 0, match.start()) for mark in ".!?;") + 1
    end_mark = _SENTENCE_END.search(prose, match.end())
    end = end_mark.start() if end_mark else len(prose)
    return bool(
        _NEGATION.search(prose[start : match.start()]) or _NEGATION.search(prose[match.end() : end])
    )


def outcome_supported(
    claim: Claim, side: int | None, outcome: OutcomeFacts, *, exact: bool = False
) -> bool:
    """Whether the line's outcome states ``claim`` (a balance or change claim) for ``side``.

    A change claim ("wins a pawn") is checked against the change over the whole
    line, a balance claim ("is up a rook", "material is equal") against the
    balance at its end. Sizes are checked as in ``supported`` (a piece or rook
    may be one pawn more), except an ``exact`` count of points, which the slot
    values state ("5 points of material"); bare "material" needs only the sign.
    """
    value = outcome.change if claim.topic == "change" else outcome.balance
    if claim.amount == SLOT_AMOUNT:
        return False
    if claim.sign == 0:
        return value == 0
    if side is None:
        return False
    gained = side * claim.sign * value
    if claim.amount is None:
        return gained > 0
    slack = 1 if claim.amount >= 3 and not exact else 0
    return claim.amount <= gained <= claim.amount + slack


# A count in points ("two points of material") is exact; a piece may be a pawn more.
_POINTS = re.compile(r"\bpoints?\b", re.IGNORECASE)


def outcome_claims(
    prose: str, outcomes: Sequence[OutcomeFacts]
) -> tuple[str, RejectedClaim | None]:
    """Admit the outcome words of the material claims in ``prose`` (#449).

    Returns ``prose`` with every ``OUTCOME_WORDS`` match blanked that lies inside
    a balance or change claim of ``CLAIM_RULES``, and the first of those claims
    that some line of ``outcomes`` does not support (a negated one included):
    a line item has its one line; ``comparison`` has every line, so a material
    claim there must hold for each of them ("all lines keep material equal"),
    since code cannot tell which line the prose means. Outcome words
    outside such a claim stay, so the vocabulary rule still rejects them
    ("this line wins for the side to move", "the material balance is better").
    """
    found: list[tuple[re.Match[str], Claim]] = []
    remaining = prose
    for rule, read in CLAIM_RULES:
        pattern = rule.compiled()
        for match in pattern.finditer(remaining):
            if not OUTCOME_WORDS.search(match[0]):
                continue
            claim = read(match)
            if claim.topic in ("balance", "change"):
                found.append((match, claim))
        remaining = pattern.sub(lambda match: " " * len(match[0]), remaining)
    spans = [match.span() for match, _ in found]
    admitted = list(prose)
    rejected: RejectedClaim | None = None
    for match, claim in sorted(found, key=lambda item: item[0].start()):
        if not _plain_sentence(prose, match, spans):
            continue  # its words stay for the vocabulary rule
        words = list(OUTCOME_WORDS.finditer(match[0]))
        groups = match.groupdict()
        negated = groups.get("neg") or groups.get("neg2") or _negated_around(prose, match)
        if rejected is None and negated:
            rejected = RejectedClaim(match[0].strip(), negated=True)
        elif rejected is None and not all(
            outcome_supported(
                claim,
                _outcome_side(claim, prose, match),
                outcome,
                exact=bool(_POINTS.search(match[0])),
            )
            for outcome in outcomes
        ):
            rejected = RejectedClaim(match[0].strip(), negated=False)
        for word in words:
            begin, end = match.start() + word.start(), match.start() + word.end()
            admitted[begin:end] = " " * (end - begin)
    return "".join(admitted), rejected


__all__ = [
    "BETTER_CP",
    "CLAIM_RULES",
    "EQUAL_CP",
    "FACTOR_TOPICS",
    "OUTCOME_WORDS",
    "WINNING_CP",
    "Claim",
    "ClaimFacts",
    "OutcomeFacts",
    "RejectedClaim",
    "factor_group",
    "outcome_claims",
    "outcome_supported",
    "supported",
    "unsupported_claim",
]
