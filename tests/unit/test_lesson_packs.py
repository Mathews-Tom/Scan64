from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from shutil import which
from typing import Any, cast
from uuid import UUID

import chess
import chess.engine
import pytest
from sqlalchemy import Engine, delete, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from scan64.api.models import Player
from scan64.chess.analysis.models import EngineAnalysis
from scan64.lesson_packs.models import LessonPack, PackLesson
from scan64.lesson_packs.schema import MAX_PACK_BYTES, LessonPackDocument, parse_pack
from scan64.lesson_packs.service import fail_interrupted_packs, verify_pack
from scan64.providers.stockfish.adapter import StockfishConfig
from scan64.providers.stockfish.pool import EnginePoolManager

LINE = ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5"]
EXAMPLE = Path(__file__).parents[2] / "docs/lesson-packs-example.json"


def document() -> dict[str, Any]:
    return {
        "format": "scan64-lesson-pack",
        "format_version": 1,
        "id": "local-lessons",
        "title": "Local lessons",
        "author": "A community author",
        "licence": "CC0-1.0",
        "version": "1.0",
        "lessons": [
            {
                "id": "develop-pieces",
                "title": "Develop pieces",
                "fen": chess.STARTING_FEN,
                "solution": list(LINE),
                "objective": "Develop your pieces.",
                "hints": ["Look for a developing move."],
                "explanation": "A piece joins the game.",
                "themes": ["development"],
            }
        ],
    }


def raw_document(value: dict[str, Any]) -> bytes:
    return json.dumps(value).encode()


def test_schema_rejects_clock99_quiet_blunder_pack() -> None:
    value = document()
    value["lessons"][0]["fen"] = chess.STARTING_FEN.replace(" 0 1", " 99 1")
    value["lessons"][0]["solution"] = ["g1h3"]
    with pytest.raises(ValueError, match="halfmove clock plus solution length"):
        parse_pack(raw_document(value))


def test_schema_rejects_clock150_fen() -> None:
    value = document()
    value["lessons"][0]["fen"] = chess.STARTING_FEN.replace(" 0 1", " 150 1")
    with pytest.raises(ValueError, match="automatically terminal"):
        parse_pack(raw_document(value))


def test_schema_accepts_clock_plus_solution_below100() -> None:
    value = document()
    value["lessons"][0]["fen"] = chess.STARTING_FEN.replace(" 0 1", " 94 1")
    parsed = parse_pack(raw_document(value))
    assert parsed.lessons[0].fen == value["lessons"][0]["fen"]
    assert parsed.lessons[0].solution == LINE


def test_schema_rejects_clock_threshold_even_with_pawn_resets() -> None:
    value = document()
    value["lessons"][0]["fen"] = chess.STARTING_FEN.replace(" 0 1", " 95 1")
    with pytest.raises(ValueError, match="halfmove clock plus solution length"):
        parse_pack(raw_document(value))


@pytest.mark.parametrize(
    "fen",
    [
        "7k/6Q1/5K2/8/8/8/8/8 b - - 0 1",
        "7k/5Q2/5K2/8/8/8/8/8 b - - 0 1",
        "7k/8/5K2/8/8/8/8/8 w - - 0 1",
    ],
    ids=["checkmate", "stalemate", "insufficient-material"],
)
def test_schema_rejects_automatic_terminal_position(fen: str) -> None:
    value = document()
    value["lessons"][0]["fen"] = fen
    with pytest.raises(ValueError, match="automatically terminal"):
        parse_pack(raw_document(value))


def test_schema_preserves_text_and_exact_size_boundary() -> None:
    value = document()
    value["description"] = "First line\nSecond line"
    value["lessons"][0]["objective"] = "First line\nSecond line"
    raw = raw_document(value)
    parsed = parse_pack(raw + b" " * (MAX_PACK_BYTES - len(raw)))
    assert parsed.description == "First line\nSecond line"
    assert parsed.lessons[0].solution == LINE
    with pytest.raises(ValueError, match="exceeds"):
        parse_pack(raw + b" " * (MAX_PACK_BYTES - len(raw) + 1))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("format_version", True),
        ("format_version", "1"),
        ("format_version", 1.0),
        ("format_version", 2),
        ("format", "scan64-code-pack"),
        ("version", 1),
        ("title", 12),
        ("title", " " * 4),
        ("title", "x" * 2001),
        ("id", "../escape"),
        ("id", "Uppercase"),
        ("source", "line\nline"),
        ("executable", "anything"),
    ],
)
def test_schema_rejects_pack_field_coercion_and_unknown_fields(
    field: str, replacement: object
) -> None:
    value = document()
    value[field] = replacement
    with pytest.raises(ValueError):
        parse_pack(raw_document(value))


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("title", "x" * 2001),
        ("solution", []),
        ("solution", LINE + ["a7a6"]),
        ("solution", [12]),
        ("hints", ["one", "two", "three", "four"]),
        ("hints", "not a list"),
        ("themes", [12]),
        ("explanation", "x" * 2001),
        ("objective", ""),
        ("fen", "not a FEN"),
        ("fen", "8/8/8/8/8/8/8/8 w - - 0 1"),
        ("fen", "8/8/8/8/8/8/8/K6k w - -"),
        ("html", "<script>not allowed as a field</script>"),
    ],
)
def test_schema_rejects_lesson_field_coercion_and_bounds(field: str, replacement: object) -> None:
    value = document()
    value["lessons"][0][field] = replacement
    with pytest.raises(ValueError):
        parse_pack(raw_document(value))


@pytest.mark.parametrize("control", ["\x00", "\t", "\r", "\x7f", "\x85", "\u202e", "\ud800"])
@pytest.mark.parametrize("field", ["title", "objective", "explanation"])
def test_schema_rejects_controls_in_all_text(control: str, field: str) -> None:
    value = document()
    value["lessons"][0][field] = f"text{control}text"
    with pytest.raises(ValueError):
        parse_pack(raw_document(value))


def test_schema_enforces_unique_ids_and_lesson_limit() -> None:
    value = document()
    lesson = value["lessons"][0]
    value["lessons"] = [dict(lesson, id=f"lesson-{index}") for index in range(50)]
    assert len(parse_pack(raw_document(value)).lessons) == 50
    value["lessons"].append(dict(lesson, id="extra"))
    with pytest.raises(ValueError):
        parse_pack(raw_document(value))
    value["lessons"] = [lesson, lesson]
    with pytest.raises(ValueError, match="Repeated lesson id"):
        parse_pack(raw_document(value))
    value["lessons"] = []
    with pytest.raises(ValueError):
        parse_pack(raw_document(value))


@pytest.mark.parametrize("raw", [b"{", b"[]", b"null", b"\xff", b'{"id":"a","id":"b"}'])
def test_schema_rejects_malformed_json(raw: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pack(raw)


def test_schema_rejects_non_json_values_and_python_coercion() -> None:
    with pytest.raises(ValueError, match="Invalid JSON constant"):
        parse_pack(b'{"format_version":NaN}')
    value = document()
    value["lessons"][0]["solution"] = tuple(LINE)
    with pytest.raises(ValueError):
        LessonPackDocument.model_validate(value)


@pytest.fixture
def pack_engine() -> Generator[Engine, None, None]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(
        engine, tables=[Player.__table__, LessonPack.__table__, PackLesson.__table__]
    )
    try:
        yield engine
    finally:
        engine.dispose()


def install(engine: Engine, value: dict[str, Any] | None = None) -> UUID:
    raw = raw_document(value if value is not None else document())
    parsed = parse_pack(raw)
    with Session(engine) as session:
        if session.get(Player, "pack-owner") is None:
            session.add(Player(id="pack-owner"))
            session.commit()
        pack = LessonPack(
            player_id="pack-owner",
            pack_id=parsed.id,
            title=parsed.title,
            author=parsed.author,
            licence=parsed.licence,
            version=parsed.version,
            sha256=sha256(raw).hexdigest(),
        )
        session.add(pack)
        session.flush()
        pack_id = pack.id
        for lesson in parsed.lessons:
            session.add(
                PackLesson(
                    pack_id=pack_id,
                    lesson_id=lesson.id,
                    title=lesson.title,
                    payload=lesson.model_dump(mode="json"),
                )
            )
        session.commit()
        return pack_id


def read_pack(engine: Engine, pack_id: UUID) -> tuple[LessonPack, list[PackLesson]]:
    with Session(engine) as session:
        pack = session.get(LessonPack, pack_id)
        assert pack is not None
        return pack, list(session.exec(select(PackLesson).where(PackLesson.pack_id == pack_id)))


def expected_positions() -> dict[str, str]:
    board = chess.Board()
    positions = {}
    for ply, move in enumerate(LINE):
        if ply % 2 == 0:
            positions[board.fen()] = move
        board.push_uci(move)
    return positions


class RecordedAdapter:
    def __init__(self) -> None:
        self.positions = expected_positions()
        self.calls: list[tuple[str, int | None, int]] = []
        self.error: Exception | None = None
        self.on_call: Callable[[], None] | None = None
        self.active = 0
        self.max_active = 0

    async def analyze_position(
        self,
        fen: str,
        nodes: int | None = None,
        depth: int | None = None,
        time_ms: int | None = None,
        multipv: int = 1,
    ) -> EngineAnalysis:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append((fen, nodes, multipv))
            await asyncio.sleep(0)
            if self.on_call:
                self.on_call()
            if self.error:
                raise self.error
            return EngineAnalysis(
                config={"nodes": nodes, "multipv": multipv},
                raw_result=[{"pv": [self.positions[fen]], "score_cp": 100}],
            )
        finally:
            self.active -= 1


class RecordedPool:
    def __init__(self, adapter: RecordedAdapter) -> None:
        self.adapter = adapter
        self.acquires = 0
        self.releases = 0
        self.error: Exception | None = None

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[RecordedAdapter, None]:
        self.acquires += 1
        if self.error:
            raise self.error
        try:
            yield self.adapter
        finally:
            self.releases += 1


def manager_for(pool: RecordedPool) -> EnginePoolManager:
    # Production service uses only the public pool lease, not per-call adapters.
    class Manager:
        batch_pool = pool

    return cast(EnginePoolManager, Manager())


@pytest.mark.asyncio
async def test_verifies_every_solver_ply_sequentially_with_one_lease(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    adapter = RecordedAdapter()
    pool = RecordedPool(adapter)
    checked_out = 0

    def checkout(*args: Any) -> None:
        nonlocal checked_out
        checked_out += 1

    def checkin(*args: Any) -> None:
        nonlocal checked_out
        checked_out -= 1

    event.listen(pack_engine, "checkout", checkout)
    event.listen(pack_engine, "checkin", checkin)

    def assert_no_open_connection() -> None:
        assert checked_out == 0
        pack, lessons = read_pack(pack_engine, pack_id)
        assert pack.state == "verifying"
        assert lessons[0].state == "pending"

    adapter.on_call = assert_no_open_connection
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified" and pack.reason is None
    assert lessons[0].state == "verified"
    assert lessons[0].failing_ply is None and lessons[0].reason is None
    assert adapter.calls == [(fen, 1_000_000, 2) for fen in expected_positions()]
    assert adapter.max_active == 1
    assert (pool.acquires, pool.releases) == (1, 1)


@pytest.mark.asyncio
async def test_reports_first_unsound_solver_ply_without_partial_verification(
    pack_engine: Engine,
) -> None:
    pack_id = install(pack_engine)
    adapter = RecordedAdapter()
    second_position = list(adapter.positions)[1]
    adapter.positions[second_position] = "d2d4"
    pool = RecordedPool(adapter)
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified"
    assert lessons[0].state == "rejected" and lessons[0].failing_ply == 3
    assert "sound move" in (lessons[0].reason or "")
    assert len(adapter.calls) == 2


@pytest.mark.parametrize(
    ("line", "failing_ply", "reason"),
    [
        (["e2e5"], 1, "illegal"),
        (["not-uci"], 1, "invalid UCI"),
        (["e2e4", "e7e5", "g1g5"], 3, "illegal"),
        (["e2e4", "e7e5"], 2, "end on a solver"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_lines_reject_lesson_not_file(
    pack_engine: Engine, line: list[str], failing_ply: int, reason: str
) -> None:
    value = document()
    value["lessons"][0]["solution"] = line
    pack_id = install(pack_engine, value)
    adapter = RecordedAdapter()
    await verify_pack(pack_engine, pack_id, manager_for(RecordedPool(adapter)))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified"
    assert lessons[0].state == "rejected"
    assert lessons[0].failing_ply == failing_ply
    assert reason in (lessons[0].reason or "")
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_unavailable_without_manager_can_be_retried(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    await verify_pack(pack_engine, pack_id, None)
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "unavailable" and "no engine pool" in (pack.reason or "")
    assert lessons[0].state == "pending"
    with Session(pack_engine) as session:
        stored = session.get(LessonPack, pack_id)
        assert stored is not None
        stored.state = "pending"
        session.commit()
    await verify_pack(pack_engine, pack_id, manager_for(RecordedPool(RecordedAdapter())))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified" and lessons[0].state == "verified"


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("stockfish"),
        PermissionError("stockfish"),
        chess.engine.EngineTerminatedError("Stockfish stopped during startup"),
    ],
)
@pytest.mark.asyncio
async def test_process_launch_failure_is_unavailable(pack_engine: Engine, error: Exception) -> None:
    pack_id = install(pack_engine)
    pool = RecordedPool(RecordedAdapter())
    pool.error = error
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "unavailable"
    assert lessons[0].state == "pending"


@pytest.mark.asyncio
async def test_engine_failure_fails_pack_instead_of_rejecting_lesson(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    adapter = RecordedAdapter()
    adapter.error = RuntimeError("engine disconnected")
    pool = RecordedPool(adapter)
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "failed" and "engine disconnected" in (pack.reason or "")
    assert lessons[0].state == "pending"
    assert pool.releases == 1


@pytest.mark.parametrize("state", ["pending", "verifying"])
def test_restart_fails_interrupted_pack_and_resets_every_lesson(
    pack_engine: Engine, state: str
) -> None:
    value = document()
    value["lessons"].append(dict(value["lessons"][0], id="another-lesson"))
    pack_id = install(pack_engine, value)
    with Session(pack_engine) as session:
        pack = session.get(LessonPack, pack_id)
        assert pack is not None
        pack.state = state
        lessons = session.exec(select(PackLesson).where(PackLesson.pack_id == pack_id)).all()
        lessons[0].state = "verified"
        lessons[1].state = "rejected"
        lessons[1].failing_ply = 2
        lessons[1].reason = "old failure"
        session.commit()
    fail_interrupted_packs(pack_engine)
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "failed" and "restart" in (pack.reason or "")
    assert all(lesson.state == "pending" for lesson in lessons)
    assert all(lesson.failing_ply is None and lesson.reason is None for lesson in lessons)
    # Idempotent startup never resets a completed pack or touches trusted tables.
    fail_interrupted_packs(pack_engine)
    assert read_pack(pack_engine, pack_id)[0].state == "failed"


@pytest.mark.asyncio
async def test_restart_preserves_completed_pack(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    await verify_pack(pack_engine, pack_id, manager_for(RecordedPool(RecordedAdapter())))
    fail_interrupted_packs(pack_engine)
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified" and lessons[0].state == "verified"


@pytest.mark.asyncio
async def test_uninstall_during_analysis_never_recreates_rows(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    adapter = RecordedAdapter()

    def uninstall() -> None:
        with Session(pack_engine) as session:
            session.execute(delete(PackLesson).where(PackLesson.pack_id == pack_id))
            session.execute(delete(LessonPack).where(LessonPack.id == pack_id))
            session.commit()

    adapter.on_call = uninstall
    pool = RecordedPool(adapter)
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    with Session(pack_engine) as session:
        assert session.get(LessonPack, pack_id) is None
        assert session.exec(select(PackLesson)).all() == []
    assert len(adapter.calls) == 1
    assert pool.releases == 1


@pytest.mark.asyncio
async def test_missing_or_completed_pack_does_not_borrow_engine(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    pool = RecordedPool(RecordedAdapter())
    manager = manager_for(pool)
    await verify_pack(pack_engine, pack_id, manager)
    await verify_pack(pack_engine, pack_id, manager)
    assert pool.acquires == 1
    with Session(pack_engine) as session:
        session.execute(delete(PackLesson).where(PackLesson.pack_id == pack_id))
        session.execute(delete(LessonPack).where(LessonPack.id == pack_id))
        session.commit()
    await verify_pack(pack_engine, pack_id, manager)
    assert pool.acquires == 1


@pytest.mark.asyncio
async def test_documented_fixture_with_real_stockfish(pack_engine: Engine) -> None:
    config = StockfishConfig()
    if which(config.binary_path) is None:
        pytest.skip("Stockfish binary is required for the real lesson-pack fixture")
    value = json.loads(EXAMPLE.read_bytes())
    pack_id = install(pack_engine, value)
    manager = EnginePoolManager(config, interactive_concurrency=1, batch_concurrency=1)
    try:
        await verify_pack(pack_engine, pack_id, manager)
    finally:
        await manager.close()
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified", pack.reason
    states = {lesson.lesson_id: lesson for lesson in lessons}
    assert states["mate-in-one"].state == "verified"
    assert states["unsound-stalemate"].state == "rejected"
    assert states["unsound-stalemate"].failing_ply == 1


@pytest.mark.parametrize("field", ["description", "source"])
def test_optional_fields_must_be_text_when_present(field: str) -> None:
    value = document()
    value[field] = None
    with pytest.raises(ValueError):
        parse_pack(raw_document(value))


@pytest.mark.asyncio
async def test_restart_requires_full_retry_before_pack_is_verified(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    with Session(pack_engine) as session:
        stored = session.get(LessonPack, pack_id)
        assert stored is not None
        stored.state = "verifying"
        lesson = session.exec(select(PackLesson).where(PackLesson.pack_id == pack_id)).one()
        lesson.state = "verified"
        session.commit()
    fail_interrupted_packs(pack_engine)
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "failed" and lessons[0].state == "pending"
    pool = RecordedPool(RecordedAdapter())
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    assert pool.acquires == 0
    with Session(pack_engine) as session:
        stored = session.get(LessonPack, pack_id)
        assert stored is not None
        stored.state = "pending"
        session.commit()
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified" and lessons[0].state == "verified"
    assert len(pool.adapter.calls) == 3


@pytest.mark.asyncio
async def test_one_lease_covers_multiple_lessons_and_rejections(pack_engine: Engine) -> None:
    value = document()
    value["lessons"].append(dict(value["lessons"][0], id="bad-line", solution=["e2e5"]))
    value["lessons"].append(dict(value["lessons"][0], id="second-sound"))
    pack_id = install(pack_engine, value)
    pool = RecordedPool(RecordedAdapter())
    await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, lessons = read_pack(pack_engine, pack_id)
    assert pack.state == "verified"
    assert {lesson.lesson_id: lesson.state for lesson in lessons} == {
        "develop-pieces": "verified",
        "bad-line": "rejected",
        "second-sound": "verified",
    }
    assert len(pool.adapter.calls) == 6 and pool.adapter.max_active == 1
    assert (pool.acquires, pool.releases) == (1, 1)


@pytest.mark.asyncio
async def test_cancellation_releases_lease_and_leaves_pack_unplayable(pack_engine: Engine) -> None:
    pack_id = install(pack_engine)
    pool = RecordedPool(RecordedAdapter())

    def cancel_verification() -> None:
        raise asyncio.CancelledError

    pool.adapter.on_call = cancel_verification
    with pytest.raises(asyncio.CancelledError):
        await verify_pack(pack_engine, pack_id, manager_for(pool))
    pack, _ = read_pack(pack_engine, pack_id)
    assert pack.state == "failed" and "interrupted" in (pack.reason or "")
    assert pool.releases == 1
