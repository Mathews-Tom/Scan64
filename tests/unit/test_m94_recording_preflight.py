from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TextIO

import pytest

from scan64.learning.evaluation import m94_protocol
from scan64.providers.stockfish.adapter import StockfishConfig
from scripts import record_diagnosis_reference as recorder


def _previous_corpora(path: Path, original_engine: str = "Stockfish 19") -> None:
    for name, start, end in [
        ("recorded.json", 0, 40),
        ("fresh80_recorded.json", 40, 120),
        ("m79_fresh80_recorded.json", 120, 200),
        ("m93_fresh80_recorded.json", 200, 280),
        ("m94_fresh80_recorded.json", 280, 360),
    ]:
        (path / name).write_text(
            json.dumps(
                {
                    "provenance": {"engine": original_engine},
                    "games": [{"id": f"synthetic-{index}"} for index in range(start, end)],
                }
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "engine,threads,hash_mb,original_engine,message",
    [
        ("Stockfish 18", 1, 16, "Stockfish 19", "Stockfish 19"),
        ("Stockfish 19", 2, 16, "Stockfish 19", "one thread and 16 MB"),
        ("Stockfish 19", 1, 32, "Stockfish 19", "one thread and 16 MB"),
        ("Stockfish 19", 1, 16, "Stockfish 18", "original engine"),
    ],
)
async def test_wrong_engine_or_config_cannot_consume_the_one_shot_holdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    threads: int,
    hash_mb: int,
    original_engine: str,
    message: str,
) -> None:
    _previous_corpora(tmp_path, original_engine)

    def source(_: Path) -> Any:
        raise AssertionError("Wrong engine/config consumed the PGN source")

    calls: list[str] = []

    class IdentityProbe:
        async def analysis_identity(self) -> dict[str, Any]:
            calls.append("identity")
            return {"engine_name": engine}

        async def analyze_position(self, *args: Any, **kwargs: Any) -> None:
            raise AssertionError("Identity preflight performed a position search")

        async def quit(self) -> None:
            calls.append("quit")

    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(recorder, "verified_pgn", source)
    monkeypatch.setattr(
        m94_protocol, "validate_frozen_sources", lambda commit, root: dict(m94_protocol._DEFINITION)
    )
    monkeypatch.setattr(
        recorder, "StockfishConfig", lambda: StockfishConfig(threads=threads, hash_size=hash_mb)
    )
    monkeypatch.setattr(recorder, "PooledStockfishAdapter", lambda config: IdentityProbe())
    with pytest.raises(ValueError, match=message):
        await recorder.record(
            tmp_path / "synthetic-source", m94_v2_fresh80=True, preregistration_commit="a" * 40
        )
    assert calls == (["identity", "quit"] if threads == 1 and hash_mb == 16 else [])
    assert not any(tmp_path.glob("m94_v2_fresh80_*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [None, "", "HEAD", "a" * 39, "b" * 64])
async def test_explicit_immutable_preregistration_is_required_before_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, commit: str | None
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Invalid preregistration consumed source or engine")

    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(recorder, "verified_pgn", forbidden)
    monkeypatch.setattr(recorder, "PooledStockfishAdapter", forbidden)
    with pytest.raises(ValueError, match="preregistration"):
        await recorder.record(
            tmp_path / "no-source", m94_v2_fresh80=True, preregistration_commit=commit
        )
    assert not any(tmp_path.glob("m94_v2_fresh80_*"))


@pytest.mark.parametrize("changed", ["protocol", "freeze", "listed", "none"])
def test_later_clean_commit_cannot_replace_explicit_preregistration_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    import hashlib

    relative_freeze = m94_protocol.FREEZE.relative_to(m94_protocol.ROOT).as_posix()
    protocol_name = "src/scan64/learning/evaluation/m94_protocol.py"
    revision_name = "synthetic/source.py"
    revisions = {revision_name: hashlib.sha256(b"original source").hexdigest()}
    committed = {
        protocol_name: b"original protocol",
        relative_freeze: json.dumps(revisions).encode(),
        revision_name: b"original source",
    }
    for name, content in committed.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    change_name = {"protocol": protocol_name, "freeze": relative_freeze, "listed": revision_name}
    if changed != "none":
        target = tmp_path / change_name[changed]
        target.write_bytes(target.read_bytes() + b" ")
    commands: list[list[str]] = []

    def git(command: list[str], **kwargs: Any) -> bytes:
        commands.append(command)
        assert kwargs["cwd"] == tmp_path
        assert "HEAD" not in command
        if command[1] == "rev-parse":
            assert command[-1] == f"{'a' * 40}^{{commit}}"
            return ("a" * 40 + "\n").encode()
        assert command[:2] == ["git", "show"]
        commit, name = command[2].split(":", 1)
        assert commit == "a" * 40
        return committed[name]

    monkeypatch.setattr(m94_protocol.subprocess, "check_output", git)
    if changed == "none":
        assert (
            m94_protocol.validate_frozen_sources("a" * 40, tmp_path)["revision"] == "m94-motifs-v2"
        )
        assert len(commands) == 4
    else:
        with pytest.raises(ValueError, match="Frozen M94"):
            m94_protocol.validate_frozen_sources("a" * 40, tmp_path)


def test_verified_archive_decompresses_the_hashed_open_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    source = tmp_path / "archive.zst"
    original = b"verified archive"
    source.write_bytes(original)
    monkeypatch.setattr(recorder, "SHA256", hashlib.sha256(original).hexdigest())
    file_digest = hashlib.file_digest

    def swapped(archive: Any, algorithm: str) -> Any:
        digest = file_digest(archive, algorithm)
        source.rename(tmp_path / "preserved.zst")
        source.write_bytes(b"replacement must not be decompressed")
        return digest

    class Decompressor:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            assert command == ["zstd", "-dc"]
            assert kwargs["stdin"].read() == original
            self.stdout = io.StringIO("verified PGN stream")

        def __enter__(self) -> Decompressor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(hashlib, "file_digest", swapped)
    monkeypatch.setattr(recorder.subprocess, "Popen", Decompressor)
    with recorder.verified_pgn(source) as stream:
        assert stream.read() == "verified PGN stream"


@pytest.mark.asyncio
async def test_wrong_resolved_commit_stops_before_source_or_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(
        m94_protocol.subprocess, "check_output", lambda *args, **kwargs: ("b" * 40).encode()
    )
    with pytest.raises(ValueError, match="exact commit"):
        await recorder.record(
            tmp_path / "no-source", m94_v2_fresh80=True, preregistration_commit="a" * 40
        )
    assert not any(tmp_path.glob("m94_v2_fresh80_*"))


@pytest.mark.parametrize("defect", ["none", "v1_prefix", "duplicate", "too_short"])
def test_v2_selection_is_exactly_disjoint_qualifying_ordinals_361_to_440(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    _previous_corpora(tmp_path)
    if defect == "v1_prefix":
        previous = tmp_path / "m94_fresh80_recorded.json"
        data = json.loads(previous.read_bytes())
        data["games"][0]["id"] = "different-exposed-identity"
        previous.write_text(json.dumps(data))
    moves = (
        "1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 Nf6 "
        "6. Ng1 Ng8 7. Nf3 Nf6 8. Ng1 Ng8 9. Nf3 Nf6 10. Ng1 Ng8 *"
    )
    ids = [f"synthetic-{index}" for index in range(439 if defect == "too_short" else 440)]
    if defect == "duplicate":
        ids[360] = ids[280]
    pgn = "\n".join(
        f'[Site "{site}"]\n[WhiteElo "1000"]\n[BlackElo "1000"]\n\n{moves}\n' for site in ids
    )

    @contextmanager
    def source(_: Path) -> Iterator[TextIO]:
        with io.StringIO(pgn) as stream:
            yield stream

    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(recorder, "verified_pgn", source)
    if defect == "none":
        selected = recorder._select_games(
            tmp_path / "synthetic-source",
            fresh80=True,
            m79_fresh80=False,
            m93_fresh80=False,
            m94_v2_fresh80=True,
        )
        assert [game.headers["Site"] for game in selected] == [
            f"synthetic-{index}" for index in range(360, 440)
        ]
    else:
        message = {
            "v1_prefix": "archived M94 v1",
            "duplicate": "duplicate or exposed",
            "too_short": "Not enough",
        }[defect]
        with pytest.raises(ValueError, match=message):
            recorder._select_games(
                tmp_path / "synthetic-source",
                fresh80=True,
                m79_fresh80=False,
                m93_fresh80=False,
                m94_v2_fresh80=True,
            )
