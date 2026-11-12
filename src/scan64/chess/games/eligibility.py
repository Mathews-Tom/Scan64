"""Common conservative eligibility for tracked, completed standard-start games."""

from scan64.chess.games.models import Game, PlaySession


def session_exclusion(game: Game, play: PlaySession | None) -> str | None:
    if play is None or play.player_id != game.owner_player_id:
        return "unknown assistance (imported or historical game)"
    if play.status != "completed" or game.result not in {"1-0", "0-1", "1/2-1/2"}:
        return "analysed before the game ended"
    if play.assistance_tracking_version != 1:
        return "unknown assistance (session was not tracked)"
    if play.coach_mode:
        return "coach mode"
    if (
        game.headers.get("FEN")
        or play.opponent_config.get("initial_fen")
        or play.opponent_config.get("custom_start") == "true"
    ):
        return "started from a studied position"
    return None
