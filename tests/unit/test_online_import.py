from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
import pytest

import scan64.chess.games.online_import as online


def pgn(
    identity: str = "abcdefgh", *, white: str = "Alice", black: str = "Bob", extra: str = ""
) -> str:
    return (
        f'[Event "Public game"]\n[Site "https://lichess.org/{identity}"]\n'
        f'[White "{white}"]\n[Black "{black}"]\n[Result "1-0"]\n{extra}\n'
        "1. e4 { [%clk 0:02:58] } e5 2. Nf3 Nc6 1-0\n\n"
    )


async def fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    source: online.Source = "lichess",
    count: int = 10,
) -> online.OnlineGames:
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await online.fetch_online_games(source, "Alice", count, client=client)


@pytest.mark.parametrize("prefix", ["", "game/"])
@pytest.mark.asyncio
async def test_lichess_requests_newest_clocks_and_preserves_original_text(prefix: str) -> None:
    first = pgn(f"{prefix}abcdefgh", extra='[UTCDate "2026.11.18"]\n')
    second = pgn(f"{prefix}ijklmnop", white="Bob", black="aLiCe")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=first + second)

    result = await fetch(respond, count=2)
    assert [game.owner_color for game in result.games] == ["white", "black"]
    assert [game.pgn for game in result.games] == [first, second]
    assert result.games[0].moves == ["e2e4", "e7e5", "g1f3", "b8c6"]
    assert result.skipped_count == 0
    request = requests[0]
    assert len(requests) == 1
    assert request.url.host == "lichess.org"
    assert request.url.path == "/api/games/user/Alice"
    assert dict(request.url.params) == {
        "max": "2",
        "clocks": "true",
        "sort": "dateDesc",
        "perfType": "ultraBullet,bullet,blitz,rapid,classical,correspondence",
    }
    assert request.headers["user-agent"].startswith("Scan64/")


@pytest.mark.asyncio
async def test_chesscom_sorts_months_and_games_and_stops_at_count() -> None:
    paths: list[str] = []
    first = pgn("abcdefgh").replace(
        '[Site "https://lichess.org/abcdefgh"]',
        '[Site "Chess.com"]\n[Link "https://www.chess.com/game/live/123"]',
    )
    second = first.replace("/123", "/124")

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.url.host == "api.chess.com"
        assert request.headers["user-agent"].startswith("Scan64/")
        if request.url.path.endswith("archives"):
            return httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/Alice/games/2026/09",
                        "https://api.chess.com/pub/player/Alice/games/2026/11",
                        "https://api.chess.com/pub/player/Alice/games/2026/10",
                    ]
                },
            )
        if request.url.path.endswith("2026/11"):
            return httpx.Response(
                200,
                json={
                    "games": [
                        {"end_time": 1, "pgn": first, "rules": "chess"},
                        {"end_time": 3, "pgn": second, "rules": "chess"},
                        {"end_time": 4, "rules": "chess960"},
                    ]
                },
            )
        pytest.fail("Fetched an older month after satisfying the requested count")

    result = await fetch(respond, "chesscom", 2)
    assert [game.pgn for game in result.games] == [second, first]
    assert result.skipped_count == 1
    assert paths == [
        "/pub/player/Alice/games/archives",
        "/pub/player/Alice/games/2026/11",
    ]


@pytest.mark.asyncio
async def test_chesscom_fetches_older_month_when_newest_is_empty() -> None:
    paths: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("archives"):
            return httpx.Response(
                200,
                json={
                    "archives": [
                        f"https://api.chess.com/pub/player/Alice/games/2026/{month}"
                        for month in ("10", "11")
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "games": []
                if request.url.path.endswith("11")
                else [
                    {"pgn": pgn(), "rules": "chess", "end_time": 1},
                ]
            },
        )

    result = await fetch(respond, "chesscom", 1)
    assert len(result.games) == 1
    assert [path.rsplit("/", 1)[-1] for path in paths] == ["archives", "11", "10"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(404, 404), (429, 429), (500, 502), (302, 502)])
@pytest.mark.parametrize("source", ["lichess", "chesscom"])
async def test_upstream_statuses_are_specific_and_redirects_are_not_followed(
    status: int,
    expected: int,
    source: online.Source,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.test/private"})

    with pytest.raises(online.OnlineImportError) as error:
        await fetch(respond, source)
    assert error.value.status_code == expected
    assert ("Lichess" if source == "lichess" else "Chess.com") in str(error.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_network_failure_is_actionable() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unavailable", request=request)

    with pytest.raises(online.OnlineImportError, match="Lichess network request failed") as error:
        await fetch(respond)
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_total_timeout_cancels_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(online, "TOTAL_TIMEOUT", 0.01)

    async def respond(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(online.OnlineImportError, match="timed out"):
            await online.fetch_online_games("lichess", "Alice", 1, client=client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "archive",
    [
        "https://attacker.test/pub/player/Alice/games/2026/11",
        "http://api.chess.com/pub/player/Alice/games/2026/11",
        "https://api.chess.com@attacker.test/pub/player/Alice/games/2026/11",
        "https://api.chess.com/pub/player/Alice/games/2026/11?redirect=evil",
        "https://api.chess.com/pub/player/Alice/games/2026/13",
        "https://api.chess.com/pub/player/Bob/games/2026/11",
        "https://api.chess.com/pub/player/Alice/games/2026/11/../../private",
    ],
)
async def test_untrusted_archive_urls_never_cause_a_second_request(archive: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"archives": [archive]})

    with pytest.raises(online.OnlineImportError, match="unsafe archive"):
        await fetch(respond, "chesscom")
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_response_size_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(online, "MAX_RESPONSE_BYTES", 100)
    with pytest.raises(online.OnlineImportError, match="size limit"):
        await fetch(lambda request: httpx.Response(200, content=b"x" * 101))


@pytest.mark.asyncio
async def test_total_size_is_bounded_across_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(online, "MAX_TOTAL_BYTES", 150)
    with pytest.raises(online.OnlineImportError, match="size limit"):
        await fetch(
            lambda request: (
                httpx.Response(
                    200,
                    json={
                        "archives": [
                            "https://api.chess.com/pub/player/Alice/games/2026/11",
                        ]
                    },
                )
                if request.url.path.endswith("archives")
                else httpx.Response(200, text=pgn())
            ),
            "chesscom",
        )


@pytest.mark.asyncio
async def test_request_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(online, "MAX_MONTHS", 1)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "archives": [
                    "https://api.chess.com/pub/player/Alice/games/2026/11",
                    "https://api.chess.com/pub/player/Alice/games/2026/10",
                ]
            }
            if request.url.path.endswith("archives")
            else {"games": []},
        )

    with pytest.raises(online.OnlineImportError, match="history limit"):
        await fetch(respond, "chesscom")
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "not PGN",
        pgn().replace("e5", "e9"),
        pgn(white="Bob"),
        pgn(black="ALICE"),
        pgn(extra='[FEN "not a position"]\n[SetUp "1"]\n'),
    ],
)
async def test_malformed_and_ambiguous_or_unmatched_owner_games_fail(raw: str) -> None:
    with pytest.raises(online.OnlineImportError) as error:
        await fetch(lambda request: httpx.Response(200, text=raw))
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_standard_setup_positions_are_accepted_and_variants_are_skipped() -> None:
    setup = pgn(extra='[SetUp "1"]\n[FEN "8/8/8/8/8/4k3/8/4K3 w - - 0 1"]\n')
    setup = setup.replace("1. e4 { [%clk 0:02:58] } e5 2. Nf3 Nc6 1-0", "1. Kf1 Kf3 1-0")
    variant = pgn("ijklmnop", extra='[Variant "Atomic"]\n')
    result = await fetch(lambda request: httpx.Response(200, text=variant + setup))
    assert result.skipped_count == 1
    assert [game.pgn for game in result.games] == [setup]


@pytest.mark.parametrize(
    "headers,expected",
    [
        (
            {"Site": "Chess.com", "Link": "https://www.chess.com/game/live/123?foo=bar"},
            "chesscom:live:123",
        ),
        ({"Site": "https://www.chess.com/live/game/123"}, "chesscom:live:123"),
        ({"Site": "https://lichess.org/abcdefgh1234/black"}, "lichess:abcdefgh"),
        ({"Site": "https://lichess.org/abcdefgh"}, "lichess:abcdefgh"),
        ({"Site": "https://attacker.test/abcdefgh"}, None),
        ({"Site": "Chess.com"}, None),
    ],
)
def test_specific_source_identity(headers: dict[str, str], expected: str | None) -> None:
    assert online.source_identity(headers) == expected


@pytest.mark.asyncio
async def test_case_insensitive_archive_username_is_safe() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("archives"):
            return httpx.Response(
                200,
                json={
                    "archives": [
                        "https://api.chess.com/pub/player/alice/games/2026/11",
                    ]
                },
            )
        return httpx.Response(200, json={"games": [{"pgn": pgn(), "end_time": 1}]})

    assert len((await fetch(respond, "chesscom", 1)).games) == 1


@pytest.mark.asyncio
async def test_count_overflow_and_per_game_size_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(online.OnlineImportError, match="more games than requested"):
        await fetch(lambda request: httpx.Response(200, text=pgn() + pgn()), count=1)
    monkeypatch.setattr(online, "MAX_PGN_BYTES", 100)
    with pytest.raises(online.OnlineImportError, match="oversized PGN"):
        await fetch(lambda request: httpx.Response(200, text=pgn()))


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [None, {}, {"archives": "not a list"}])
async def test_invalid_chesscom_data_is_actionable(data: object) -> None:
    with pytest.raises(online.OnlineImportError, match="invalid"):
        await fetch(lambda request: httpx.Response(200, json=data), "chesscom")


@pytest.mark.asyncio
async def test_brace_comments_with_header_like_text_preserve_raw_pgn() -> None:
    raw = pgn().replace("{ [%clk 0:02:58] }", '{ [%clk 0:02:58]\n[Event "not a game"]\n}')
    result = await fetch(lambda request: httpx.Response(200, text=raw + pgn("ijklmnop")))
    assert result.games[0].pgn == raw
    assert result.games[1].pgn == pgn("ijklmnop")
