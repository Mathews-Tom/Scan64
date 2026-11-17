"""Explicit, bounded imports of public standard games from two fixed providers."""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import chess.pgn
import httpx

Source = Literal["lichess", "chesscom"]
USERNAME = re.compile(r"[A-Za-z0-9_-]{2,30}\Z", re.ASCII)
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PGN_BYTES = 128 * 1024
MAX_MONTHS = 24
REQUEST_TIMEOUT = 10.0
TOTAL_TIMEOUT = 30.0
USER_AGENT = "Scan64/online-game-import (+https://github.com/Mathews-Tom/Scan64)"
STANDARD_VARIANTS = {"standard", "chess", "normal", "from position"}


class OnlineImportError(ValueError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class OnlineGame:
    pgn: str
    headers: dict[str, str]
    moves: list[str]
    owner_color: Literal["white", "black"]


@dataclass(frozen=True)
class OnlineGames:
    games: list[OnlineGame]
    skipped_count: int


def source_identity(headers: Mapping[str, str]) -> str | None:
    """Use a game-specific link, not Chess.com's shared Site header."""
    for key in ("Link", "Site"):
        value = headers.get(key, "").strip()
        try:
            url = urlsplit(value)
        except ValueError:
            continue
        host = (url.hostname or "").lower()
        path = url.path.rstrip("/")
        if host in {"chess.com", "www.chess.com"}:
            match = re.fullmatch(r"/(?:game/(live|daily)|(live|daily)/game)/(\d+)", path)
            if match:
                kind = match[1] or match[2]
                return f"chesscom:{kind}:{match[3]}"
        elif host in {"lichess.org", "www.lichess.org"}:
            match = re.fullmatch(
                r"/(?:game/)?([A-Za-z0-9]{8})(?:[A-Za-z0-9]{4})?(?:/(?:white|black))?", path
            )
            if match:
                return f"lichess:{match[1]}"
    return None


class _StrictGameBuilder(chess.pgn.GameBuilder[chess.pgn.Game]):
    def handle_error(self, error: Exception) -> None:
        raise ValueError("Invalid PGN moves") from error


def _parse_game(raw: str, username: str, label: str) -> OnlineGame | None:
    if len(raw.encode("utf-8")) > MAX_PGN_BYTES:
        raise OnlineImportError(502, f"{label} returned an oversized PGN game.")
    try:
        headers = chess.pgn.read_headers(io.StringIO(raw))
        if headers is None:
            raise ValueError("Missing PGN headers")
        if headers.get("Variant", "Standard").casefold() not in STANDARD_VARIANTS:
            return None
        parsed = chess.pgn.read_game(io.StringIO(raw), Visitor=_StrictGameBuilder)
        if parsed is None or not parsed.board().is_valid() or parsed.board().chess960:
            raise ValueError("Invalid standard starting position")
        moves = [move.uci() for move in parsed.mainline_moves()]
        if not moves or parsed.headers.get("Result") not in {"*", "1-0", "0-1", "1/2-1/2"}:
            raise ValueError("Missing moves or invalid result")
    except (ValueError, IndexError) as error:
        raise OnlineImportError(400, f"{label} returned malformed or unsupported PGN.") from error
    white_matches = parsed.headers.get("White", "").casefold() == username.casefold()
    black_matches = parsed.headers.get("Black", "").casefold() == username.casefold()
    if white_matches == black_matches:
        raise OnlineImportError(
            400, f"{label} PGN does not uniquely identify {username} as a player."
        )
    if source_identity(dict(parsed.headers)) is None:
        raise OnlineImportError(
            400, f"{label} PGN is missing a supported game Site or Link identity."
        )
    return OnlineGame(raw, dict(parsed.headers), moves, "white" if white_matches else "black")


def _split_pgn(text: str, label: str) -> list[str]:
    stream = io.StringIO(text)
    games: list[str] = []
    while stream.tell() < len(text):
        start = stream.tell()
        try:
            headers = chess.pgn.read_headers(stream)
        except (ValueError, IndexError) as error:
            raise OnlineImportError(400, f"{label} returned malformed PGN.") from error
        if headers is None:
            if text[start:].strip():
                raise OnlineImportError(400, f"{label} returned malformed PGN.")
            break
        games.append(text[start : stream.tell()])
        if len(games) > 50:
            raise OnlineImportError(502, f"{label} returned more games than requested.")
    return games


class _Provider:
    def __init__(self, client: httpx.AsyncClient, source: Source, username: str) -> None:
        self.client = client
        self.source = source
        self.username = username
        self.label = "Lichess" if source == "lichess" else "Chess.com"
        self.total_bytes = 0

    async def get(self, suffix: str, params: dict[str, str | int] | None = None) -> bytes:
        if self.source == "lichess":
            url = f"https://lichess.org/api/games/user/{self.username}"
        else:
            url = f"https://api.chess.com/pub/player/{self.username}/games/{suffix}"
        async with self.client.stream(
            "GET",
            url,
            params=params,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if response.status_code == 404:
                raise OnlineImportError(404, f"{self.label} username was not found.")
            if response.status_code == 429:
                raise OnlineImportError(429, f"{self.label} rate limit reached. Try again later.")
            if response.status_code != 200:
                raise OnlineImportError(502, f"{self.label} request failed. Try again later.")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise OnlineImportError(
                    502, f"{self.label} returned unsupported response encoding."
                )
            content = bytearray()
            async for chunk in response.aiter_bytes(64 * 1024):
                self.total_bytes += len(chunk)
                if (
                    len(content) + len(chunk) > MAX_RESPONSE_BYTES
                    or self.total_bytes > MAX_TOTAL_BYTES
                ):
                    raise OnlineImportError(
                        502, f"{self.label} response exceeded the import size limit."
                    )
                content.extend(chunk)
            return bytes(content)

    async def get_json(self, suffix: str) -> dict[str, object]:
        raw = await self.get(suffix)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeError) as error:
            raise OnlineImportError(502, f"{self.label} returned invalid game data.") from error
        if not isinstance(data, dict):
            raise OnlineImportError(502, f"{self.label} returned invalid game data.")
        return data

    async def fetch(self, max_games: int) -> OnlineGames:
        if self.source == "lichess":
            raw = await self.get(
                "",
                {
                    "max": max_games,
                    "clocks": "true",
                    "sort": "dateDesc",
                    "perfType": "ultraBullet,bullet,blitz,rapid,classical,correspondence",
                },
            )
            try:
                pgns = _split_pgn(raw.decode("utf-8-sig"), self.label)
            except UnicodeError as error:
                raise OnlineImportError(502, "Lichess returned invalid PGN encoding.") from error
            if len(pgns) > max_games:
                raise OnlineImportError(502, "Lichess returned more games than requested.")
            return self.parse(pgns, max_games)

        data = await self.get_json("archives")
        archives = data.get("archives")
        if not isinstance(archives, list):
            raise OnlineImportError(502, "Chess.com returned invalid archive data.")
        months: set[tuple[int, int]] = set()
        prefix = f"https://api.chess.com/pub/player/{self.username}/games/"
        for archive in archives:
            if (
                not isinstance(archive, str)
                or archive[: len(prefix)].casefold() != prefix.casefold()
            ):
                raise OnlineImportError(502, "Chess.com returned an unsafe archive URL.")
            match = re.fullmatch(r"([0-9]{4})/(0[1-9]|1[0-2])", archive[len(prefix) :])
            if match is None or not 2000 <= int(match[1]) <= 9999:
                raise OnlineImportError(502, "Chess.com returned an unsafe archive URL.")
            months.add((int(match[1]), int(match[2])))
        games: list[OnlineGame] = []
        skipped = 0
        for index, (year, month) in enumerate(sorted(months, reverse=True)):
            if index >= MAX_MONTHS:
                raise OnlineImportError(
                    502, "Chess.com import history limit reached. Request fewer games."
                )
            monthly = await self.get_json(f"{year:04d}/{month:02d}")
            items = monthly.get("games")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise OnlineImportError(502, "Chess.com returned invalid monthly game data.")
            # end_time is the provider's ordering key, not the month response order.
            for item in items:
                if not isinstance(item.get("end_time"), int):
                    raise OnlineImportError(502, "Chess.com returned invalid game timestamps.")
            items.sort(key=lambda item: item["end_time"], reverse=True)
            for item in items:
                if item.get("rules", "chess") != "chess":
                    skipped += 1
                    continue
                pgn = item.get("pgn")
                if not isinstance(pgn, str) or len(_split_pgn(pgn, self.label)) != 1:
                    raise OnlineImportError(400, "Chess.com returned malformed PGN.")
                parsed = _parse_game(pgn, self.username, self.label)
                if parsed is None:
                    skipped += 1
                else:
                    games.append(parsed)
                if len(games) == max_games:
                    return OnlineGames(games, skipped)
        return OnlineGames(games, skipped)

    def parse(self, pgns: list[str], max_games: int) -> OnlineGames:
        games: list[OnlineGame] = []
        skipped = 0
        for raw in pgns:
            parsed = _parse_game(raw, self.username, self.label)
            if parsed is None:
                skipped += 1
            else:
                games.append(parsed)
            if len(games) == max_games:
                break
        return OnlineGames(games, skipped)


async def fetch_online_games(
    source: Source, username: str, max_games: int, *, client: httpx.AsyncClient | None = None
) -> OnlineGames:
    if (
        USERNAME.fullmatch(username) is None
        or type(max_games) is not int
        or not 1 <= max_games <= 50
    ):
        raise OnlineImportError(422, "Use a valid username and a game count between 1 and 50.")
    if source not in {"lichess", "chesscom"}:
        raise OnlineImportError(422, "Choose Lichess or Chess.com.")
    label = "Lichess" if source == "lichess" else "Chess.com"
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            if client is not None:
                return await _Provider(client, source, username).fetch(max_games)
            async with httpx.AsyncClient(follow_redirects=False, timeout=REQUEST_TIMEOUT) as http:
                return await _Provider(http, source, username).fetch(max_games)
    except (httpx.HTTPError, TimeoutError) as error:
        raise OnlineImportError(
            502, f"{label} network request failed or timed out. Try again later."
        ) from error
