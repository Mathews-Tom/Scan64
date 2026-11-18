from __future__ import annotations

import hashlib
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


def _synthetic_recording_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    _previous_corpora(tmp_path)
    freeze = tmp_path / "synthetic_revisions.json"
    freeze.write_bytes(json.dumps({"synthetic.py": hashlib.sha256(b"source").hexdigest()}).encode())
    monkeypatch.setattr(m94_protocol, "FREEZE", freeze)
    monkeypatch.setattr(
        m94_protocol, "validate_frozen_sources", lambda commit, root: m94_protocol.get_protocol()
    )
    moves = (
        "1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 Nf6 "
        "6. Ng1 Ng8 7. Nf3 Nf6 8. Ng1 Ng8 9. Nf3 Nf6 10. Ng1 Ng8 *"
    )
    pgn = "\n".join(
        f'[Site "synthetic-{index}{("-José-棋" if index >= 360 else "")}"]\n'
        f'[White "José"]\n[Black "棋"]\n[WhiteElo "1000"]\n[BlackElo "1000"]\n\n{moves}\n'
        for index in range(440)
    )
    calls = {"source": 0, "engine": 0, "analysis": 0, "quit": 0}

    @contextmanager
    def source(_: Path) -> Iterator[TextIO]:
        calls["source"] += 1
        assert calls["source"] == 1, "Finalization reopened the source"
        with io.StringIO(pgn) as stream:
            yield stream

    class SyntheticEngine:
        async def analysis_identity(self) -> dict[str, Any]:
            return {"engine_name": "Stockfish 19"}

        async def reset(self) -> None:
            assert calls["quit"] == 0, "Finalization reset the engine"

        async def analyze_position(
            self, fen: str, nodes: int, multipv: int = 1
        ) -> recorder.EngineAnalysis:
            assert calls["quit"] == 0, "Finalization performed a position search"
            assert nodes == 10_000 and multipv == 1
            calls["analysis"] += 1
            return recorder.EngineAnalysis(
                config={"engine_name": "Stockfish 19"}, raw_result=[{"score_cp": 0, "pv": []}]
            )

        async def quit(self) -> None:
            calls["quit"] += 1

    def engine(config: StockfishConfig) -> SyntheticEngine:
        calls["engine"] += 1
        assert calls["engine"] == 1, "Finalization opened another engine"
        return SyntheticEngine()

    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(recorder, "verified_pgn", source)
    monkeypatch.setattr(
        recorder, "StockfishConfig", lambda: StockfishConfig(threads=1, hash_size=16)
    )
    monkeypatch.setattr(recorder, "PooledStockfishAdapter", engine)
    return calls


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


@pytest.mark.parametrize("mutation", ["in_place", "path_replacement"])
def test_verified_archive_decompresses_private_copy_despite_original_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    source = tmp_path / "archive.zst"
    original = b"verified archive"
    source.write_bytes(original)
    original_inode = source.stat().st_ino
    monkeypatch.setattr(recorder, "SHA256", hashlib.sha256(original).hexdigest())
    archives: list[Any] = []

    class Decompressor:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            assert command == ["zstd", "-dc"]
            if mutation == "path_replacement":
                source.rename(tmp_path / "preserved.zst")
            source.write_bytes(b"replacement must not be decompressed")
            if mutation == "in_place":
                assert source.stat().st_ino == original_inode
            archive = kwargs["stdin"]
            archives.append(archive)
            assert archive.tell() == 0
            assert archive.read() == original
            self.stdout = io.StringIO("verified PGN stream")

        def __enter__(self) -> Decompressor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(recorder.subprocess, "Popen", Decompressor)
    with recorder.verified_pgn(source) as stream:
        assert stream.read() == "verified PGN stream"
    assert len(archives) == 1
    assert archives[0].closed


@pytest.mark.parametrize("failure", ["checksum", "decompressor", "consumer"])
def test_private_archive_closes_and_leaves_no_copy_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = tmp_path / "archive.zst"
    original = b"verified archive"
    source.write_bytes(original)
    monkeypatch.setattr(
        recorder,
        "SHA256",
        hashlib.sha256(b"wrong" if failure == "checksum" else original).hexdigest(),
    )
    temporary_file = recorder.tempfile.TemporaryFile
    archives: list[Any] = []

    def private_copy(*args: Any, **kwargs: Any) -> Any:
        archive = temporary_file(*args, **kwargs, dir=tmp_path)
        archives.append(archive)
        return archive

    class Decompressor:
        def __init__(self, command: list[str], **kwargs: Any) -> None:
            assert failure != "checksum", "Unverified archive reached the decompressor"
            if failure == "decompressor":
                raise RuntimeError("Synthetic decompressor failure")
            self.stdout = io.StringIO("verified PGN stream")

        def __enter__(self) -> Decompressor:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def terminate(self) -> None:
            pass

    monkeypatch.setattr(recorder.tempfile, "TemporaryFile", private_copy)
    monkeypatch.setattr(recorder.subprocess, "Popen", Decompressor)
    error = ValueError if failure == "checksum" else RuntimeError
    with pytest.raises(error, match="SHA256|Synthetic"):
        with recorder.verified_pgn(source):
            raise RuntimeError("Synthetic consumer failure")
    assert len(archives) == 1
    assert archives[0].closed
    assert list(tmp_path.iterdir()) == [source]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding,newline", [("utf-8", "\r\n"), ("cp1252", None)])
async def test_recording_publishes_exact_utf8_bytes_independent_of_text_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    encoding: str,
    newline: str | None,
) -> None:
    calls = _synthetic_recording_run(tmp_path, monkeypatch)
    prefix = m94_protocol.PREFIX
    modes: dict[str, str] = {}
    path_open = Path.open
    output_names = {f"{prefix}_real_games.pgn", f"{prefix}_recorded.json"}

    def platform_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if path.name in output_names and mode[0] in "wx":
            modes[path.name] = mode
            if "b" not in mode:
                kwargs.update(encoding=encoding, newline=newline)
        return path_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", platform_open)
    await recorder.record(
        tmp_path / "synthetic-source", m94_v2_fresh80=True, preregistration_commit="a" * 40
    )
    recording = (tmp_path / f"{prefix}_recorded.json").read_bytes()
    pgn = (tmp_path / f"{prefix}_real_games.pgn").read_bytes()
    manifest = json.loads((tmp_path / f"{prefix}_manifest.json").read_bytes())
    corpus = json.loads(recording)
    assert modes == {name: "xb" for name in output_names}
    assert "José-棋".encode() in pgn
    assert b"\r" not in pgn and pgn.endswith(b"\n")
    assert b"\r" not in recording and recording.endswith(b"\n")
    assert corpus["games"][0]["id"] == "synthetic-360-José-棋"
    assert corpus["games"][-1]["id"] == "synthetic-439-José-棋"
    assert manifest["recording_sha256"] == hashlib.sha256(recording).hexdigest()
    assert manifest["pgn_sha256"] == hashlib.sha256(pgn).hexdigest()
    assert corpus["provenance"]["pgn_sha256"] == manifest["pgn_sha256"]
    assert calls == {"source": 1, "engine": 1, "analysis": 80 * 21, "quit": 1}


def test_original_recording_uses_binary_utf8_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = {"games": [{"id": "José-棋", "note": "first\nsecond"}]}
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    path_open = Path.open
    modes = []

    def platform_open(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if path.name == "recorded.json" and mode[0] == "w":
            modes.append(mode)
            if "b" not in mode:
                kwargs.update(encoding="cp1252", newline="\r\n")
        return path_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", platform_open)
    recorder.save_recording(data)
    assert (tmp_path / "recorded.json").read_bytes() == (
        json.dumps(data, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    assert modes == ["wb"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["status", "publication", "both"])
async def test_completed_outputs_finalize_despite_status_failure_and_preserve_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls = _synthetic_recording_run(tmp_path, monkeypatch)
    statuses: list[str] = []

    def status(message: str, *, flush: bool) -> None:
        if message.startswith("Complete M94 v2 outputs:"):
            statuses.append(message)
            if failure in {"status", "both"}:
                raise BrokenPipeError("Synthetic status pipe failure")

    def failed_publication(*args: Any, **kwargs: Any) -> None:
        raise OSError("Synthetic manifest publication failure")

    monkeypatch.setattr(recorder, "print", status, raising=False)
    if failure in {"publication", "both"}:
        monkeypatch.setattr(recorder.os, "link", failed_publication)
    error = BrokenPipeError if failure == "status" else OSError
    message = "status pipe" if failure == "status" else "manifest publication"
    with pytest.raises(error, match=message) as caught:
        await recorder.record(
            tmp_path / "synthetic-source", m94_v2_fresh80=True, preregistration_commit="a" * 40
        )
    prefix = m94_protocol.PREFIX
    recording = (tmp_path / f"{prefix}_recorded.json").read_bytes()
    pgn = (tmp_path / f"{prefix}_real_games.pgn").read_bytes()
    recording_digest = hashlib.sha256(recording).hexdigest()
    pgn_digest = hashlib.sha256(pgn).hexdigest()
    assert statuses == [
        f"Complete M94 v2 outputs: recording_sha256={recording_digest} "
        f"pgn_sha256={pgn_digest} preregistration_commit={'a' * 40}"
    ]
    manifest_path = tmp_path / f"{prefix}_manifest.json"
    if failure == "status":
        manifest = json.loads(manifest_path.read_bytes())
        assert manifest["recording_sha256"] == recording_digest
        assert manifest["pgn_sha256"] == pgn_digest
    else:
        assert not manifest_path.exists()
        assert not list(tmp_path.glob(".m94-manifest-*"))
        if failure == "both":
            assert isinstance(caught.value.__context__, BrokenPipeError)
    assert calls == {"source": 1, "engine": 1, "analysis": 80 * 21, "quit": 1}
