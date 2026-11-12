"""Named bot levels, calibrated relative to each other rather than human Elo."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BotLevel:
    level: int
    name: str
    description: str
    error_budget_cp: int


BOT_LEVELS: tuple[BotLevel, ...] = (
    BotLevel(1, "First steps", "A forgiving opponent for learning how the pieces move.", 3000),
    BotLevel(2, "Beginner", "Practise spotting hanging pieces and simple threats.", 1800),
    BotLevel(3, "Developing", "Build sound opening habits and watch for tactics.", 1000),
    BotLevel(4, "Improving", "A steadier opponent that still makes mistakes.", 600),
    BotLevel(5, "Club", "A challenging engine opponent with fewer large mistakes.", 300),
    BotLevel(6, "Advanced", "Sharper calculation with fewer unforced mistakes.", 150),
    BotLevel(7, "Expert", "A demanding opponent for experienced players.", 60),
    BotLevel(8, "Master", "The strongest opponent in this ladder.", 0),
)


def get_bot_level(level: int) -> BotLevel:
    for bot in BOT_LEVELS:
        if bot.level == level:
            return bot
    raise ValueError(f"Unknown bot level: {level}")
