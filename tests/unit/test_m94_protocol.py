from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import chess
import chess.engine
import chess.pgn
import pytest

from scan64.learning.diagnosis import trust
from scan64.learning.evaluation import m94_protocol, real_games
from scripts import record_diagnosis_reference as recorder


@pytest.fixture(autouse=True)
def synthetic_freeze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Synthetic tests do not depend on candidate or measured source manifests."""
    freeze = tmp_path / "synthetic_revisions.json"
    freeze.write_text(json.dumps({"synthetic/source.py": hashlib.sha256(b"source").hexdigest()}))
    monkeypatch.setattr(m94_protocol, "FREEZE", freeze)


def _provenance() -> dict[str, Any]:
    return {
        **m94_protocol.get_protocol(),
        "protocol": m94_protocol.get_protocol(),
        "protocol_sha256": m94_protocol.protocol_digest(),
        "preregistration_commit": "a" * 40,
    }


def _identity() -> m94_protocol.MeasurementIdentity:
    synthetic_digest = hashlib.sha256(b"synthetic-only").hexdigest()
    return m94_protocol.MeasurementIdentity(
        preregistration_commit="a" * 40,
        recording_sha256=synthetic_digest,
        pgn_sha256=synthetic_digest,
        manifest_sha256=synthetic_digest,
        protocol_sha256=m94_protocol.protocol_digest(),
        source_revisions_sha256=hashlib.sha256(m94_protocol.FREEZE.read_bytes()).hexdigest(),
    )


def _manifest(expected: m94_protocol.MeasurementIdentity) -> dict[str, str]:
    return {
        "preregistration_commit": expected.preregistration_commit,
        "protocol_sha256": expected.protocol_sha256,
        "source_revisions_sha256": expected.source_revisions_sha256,
        "recording_sha256": expected.recording_sha256,
        "pgn_sha256": expected.pgn_sha256,
    }


@pytest.mark.parametrize(
    "field",
    [
        *m94_protocol._DEFINITION,
        "revisions",
        "protocol",
        "protocol_sha256",
        "preregistration_commit",
    ],
)
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_frozen_provenance_rejects_every_changed_field(field: str, mutation: str) -> None:
    corpus = {"provenance": _provenance(), "games": [{}] * 80}
    expected = _identity()
    manifest = _manifest(expected)
    m94_protocol.validate_provenance(corpus, manifest, expected)
    altered = deepcopy(corpus)
    # Protocol-only fields are authoritative inside the frozen protocol envelope.
    target = (
        altered["provenance"]
        if field in {"protocol", "protocol_sha256", "preregistration_commit"}
        else altered["provenance"]["protocol"]
    )
    if mutation == "missing":
        del target[field]
    else:
        target[field] = "changed"
    with pytest.raises(ValueError, match="provenance"):
        m94_protocol.validate_provenance(altered, manifest, expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["recorded.json", "real_games.pgn", "manifest.json"])
async def test_recorder_refuses_any_consumed_m94_artifact_before_source_or_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    (tmp_path / f"m94_v2_fresh80_{suffix}").write_text("consumed")
    with pytest.raises(ValueError, match="overwrite"):
        await recorder.record(tmp_path / "no-source.zst", m94_v2_fresh80=True)


@pytest.mark.asyncio
async def test_unmeasured_corpus_cannot_choose_its_own_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "m94_v2_fresh80_recorded.json"
    monkeypatch.setattr(real_games, "M94_CORPUS", path)
    monkeypatch.setattr(real_games, "M94_MEASUREMENT", None)
    with pytest.raises(ValueError, match="has not been pinned"):
        await real_games.measure(path)


def _synthetic_recording(tmp_path: Path) -> tuple[Path, m94_protocol.MeasurementIdentity]:
    """Legal unscored synthetic PGNs; no engine labels or real holdout games."""
    games = []
    pgns = []
    sans = ["Nf3", "Nf6", "Ng1", "Ng8"] * 5
    for index in range(80):
        game = chess.pgn.Game()
        game.headers.update({"Site": f"synthetic-{index}", "WhiteElo": "1000", "BlackElo": "1000"})
        node: chess.pgn.GameNode = game
        board = game.board()
        for san in sans:
            move = board.parse_san(san)
            node = node.add_variation(move)
            board.push(move)
        pgns.append(
            game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False))
        )
        games.append(
            {
                "id": f"synthetic-{index}",
                "moves": sans,
                "fast": [[{"score_cp": 0, "pv": []}] for _ in range(len(sans) + 1)],
                "focused": {},
                "deep_before": {},
                "sources": {},
            }
        )
    pgn = "\n\n".join(pgns) + "\n"
    provenance = _provenance()
    provenance["pgn_sha256"] = hashlib.sha256(pgn.encode()).hexdigest()
    output = json.dumps({"provenance": provenance, "games": games})
    path = tmp_path / "m94_v2_fresh80_recorded.json"
    path.write_text(output)
    path.with_name("m94_v2_fresh80_real_games.pgn").write_text(pgn)
    expected = replace(
        _identity(),
        recording_sha256=hashlib.sha256(output.encode()).hexdigest(),
        pgn_sha256=provenance["pgn_sha256"],
    )
    manifest = json.dumps(_manifest(expected))
    path.with_name("m94_v2_fresh80_manifest.json").write_text(manifest)
    expected = replace(expected, manifest_sha256=hashlib.sha256(manifest.encode()).hexdigest())
    return path, expected


@pytest.mark.parametrize("suffix", ["recorded.json", "real_games.pgn", "manifest.json"])
@pytest.mark.parametrize("mutation", ["missing", "changed"])
def test_offline_replay_requires_every_unchanged_artifact(
    tmp_path: Path, suffix: str, mutation: str
) -> None:
    path, identity = _synthetic_recording(tmp_path)
    assert len(m94_protocol.validate_recording(path, (), identity)["games"]) == 80
    target = path.with_name(f"m94_v2_fresh80_{suffix}")
    if mutation == "missing":
        target.unlink()
        expected: type[Exception] = FileNotFoundError
    else:
        if suffix == "manifest.json":
            manifest = json.loads(target.read_text())
            manifest["recording_sha256"] = "changed"
            target.write_text(json.dumps(manifest))
        else:
            target.write_text(target.read_text() + " ")
        expected = ValueError
    with pytest.raises(expected):
        m94_protocol.validate_recording(path, (), identity)


def test_offline_replay_rejects_previously_exposed_identity(
    tmp_path: Path,
) -> None:
    path, identity = _synthetic_recording(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_text(json.dumps({"games": [{"id": "synthetic-0"}]}))
    with pytest.raises(ValueError, match="previously exposed"):
        m94_protocol.validate_recording(path, (prior,), identity)


@pytest.mark.asyncio
async def test_pinned_source_only_replay_needs_neither_git_history_nor_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, identity = _synthetic_recording(tmp_path)

    def forbidden_git(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline replay attempted Git")

    async def forbidden_engine(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Offline replay attempted an engine")

    monkeypatch.setattr(m94_protocol.subprocess, "run", forbidden_git)
    monkeypatch.setattr(m94_protocol.subprocess, "check_output", forbidden_git)
    monkeypatch.setattr(chess.engine, "popen_uci", forbidden_engine)
    prior_paths = ("CORPUS", "FRESH_CORPUS", "M79_CORPUS", "M93_CORPUS", "M94_V1_CORPUS")
    for name in prior_paths:
        prior = tmp_path / f"{name}.json"
        prior.write_text(json.dumps({"games": []}))
        monkeypatch.setattr(real_games, name, prior)
    monkeypatch.setattr(real_games, "M94_CORPUS", path)
    monkeypatch.setattr(real_games, "M94_MEASUREMENT", identity)
    scores = await real_games.measure(path)
    assert set(scores) == set(real_games.SEED_CODES)
    assert all(score.support == score.fn == 0 for score in scores.values())


@pytest.mark.parametrize(
    "field",
    [
        "preregistration_commit",
        "recording_sha256",
        "pgn_sha256",
        "manifest_sha256",
        "protocol_sha256",
        "source_revisions_sha256",
    ],
)
def test_replay_refuses_wrong_expected_identity(tmp_path: Path, field: str) -> None:
    path, identity = _synthetic_recording(tmp_path)
    altered = replace(identity, **{field: "b" * (40 if field == "preregistration_commit" else 64)})
    with pytest.raises(ValueError, match="pinned|provenance"):
        m94_protocol.validate_recording(path, (), altered)


def test_consistently_rewritten_artifacts_cannot_redefine_pinned_measurement(
    tmp_path: Path,
) -> None:
    path, identity = _synthetic_recording(tmp_path)
    corpus = json.loads(path.read_text())
    corpus["provenance"]["preregistration_commit"] = "b" * 40
    path.write_text(json.dumps(corpus))
    manifest_path = path.with_name("m94_v2_fresh80_manifest.json")
    manifest = json.loads(manifest_path.read_text())
    manifest["preregistration_commit"] = "b" * 40
    manifest["recording_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="pinned measurement"):
        m94_protocol.validate_recording(path, (), identity)


@pytest.mark.parametrize("code", sorted(m94_protocol.CODES))
@pytest.mark.parametrize("failing", ["none", "M94", "original", "M93", "unknown"])
def test_each_new_promotion_requires_its_own_gate_without_weakening_old_gates(
    monkeypatch: pytest.MonkeyPatch, failing: str, code: str
) -> None:
    declared = trust.TRUSTED_CODES | {code}
    if failing == "unknown":
        declared |= {"unmeasured"}
    monkeypatch.setattr(trust, "TRUSTED_CODES", declared)
    monkeypatch.setattr("sys.argv", ["real_games", "--check"])

    async def measured(path: Path = real_games.CORPUS) -> dict[str, real_games.Score]:
        scores = {skill: real_games.Score(tp=10) for skill in real_games.SEED_CODES}
        if path != real_games.M94_CORPUS:
            scores[code] = real_games.Score()
        if failing == "M94" and path == real_games.M94_CORPUS:
            scores[code] = real_games.Score(tp=7, fp=3)
        if failing == "original" and path == real_games.CORPUS:
            scores["board_awareness.hanging_piece"] = real_games.Score(tp=7, fp=3)
        if failing == "M93" and path == real_games.M93_CORPUS:
            scores["tactics.fork.knight"] = real_games.Score(tp=7, fp=3)
        return scores

    monkeypatch.setattr(real_games, "measure", measured)
    if failing == "none":
        real_games.main()
    else:
        message = {
            "M94": "M94 fresh80",
            "M93": "M93 fresh80",
            "original": "Original",
            "unknown": "no declared",
        }
        with pytest.raises(SystemExit, match=message[failing]):
            real_games.main()


def test_replay_hashes_and_parses_the_same_single_read_buffers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, identity = _synthetic_recording(tmp_path)
    artifacts = {
        path,
        path.with_name("m94_v2_fresh80_manifest.json"),
        path.with_name("m94_v2_fresh80_real_games.pgn"),
    }
    reads = dict.fromkeys(artifacts, 0)
    read_bytes = Path.read_bytes
    read_text = Path.read_text

    def once(target: Path) -> bytes:
        if target in artifacts:
            reads[target] += 1
            if reads[target] > 1:
                raise AssertionError("Artifact reopened after checksum")
        return read_bytes(target)

    def substituted(target: Path, *args: Any, **kwargs: Any) -> str:
        if target in artifacts:
            raise AssertionError("Parsed a second, unhashed artifact buffer")
        return read_text(target, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", once)
    monkeypatch.setattr(Path, "read_text", substituted)
    assert len(m94_protocol.validate_recording(path, (), identity)["games"]) == 80
    assert set(reads.values()) == {1}


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("missing_manifest", [False, True])
async def test_current_corpus_path_normalization_never_bypasses_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    relative: bool,
    missing_manifest: bool,
) -> None:
    path, identity = _synthetic_recording(tmp_path)
    for name in ("CORPUS", "FRESH_CORPUS", "M79_CORPUS", "M93_CORPUS", "M94_V1_CORPUS"):
        prior = tmp_path / f"{name}.json"
        prior.write_text(json.dumps({"games": []}))
        monkeypatch.setattr(real_games, name, prior)
    monkeypatch.setattr(real_games, "M94_CORPUS", path)
    monkeypatch.setattr(real_games, "M94_MEASUREMENT", identity)
    monkeypatch.chdir(tmp_path)
    if missing_manifest:
        path.with_name("m94_v2_fresh80_manifest.json").unlink()
    supplied = Path(path.name) if relative else path
    if missing_manifest:
        with pytest.raises(FileNotFoundError):
            await real_games.measure(supplied)
    else:
        absolute = await real_games.measure(path)
        assert await real_games.measure(supplied) == absolute
        assert m94_protocol.CODES <= absolute.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", [False, True])
async def test_archived_v1_is_rejected_without_opening_or_rescoring(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: bool
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "m94_fresh80_recorded.json"
    with pytest.raises(ValueError, match="Archived M94 v1"):
        await real_games.measure(Path(path.name) if relative else path)
    with pytest.raises(ValueError, match="Archived M94 v1"):
        m94_protocol.validate_recording(path, (), _identity())


@pytest.mark.asyncio
async def test_historical_measurement_omits_unmeasured_new_codes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "synthetic_historical.json"
    path.write_text(json.dumps({"provenance": {"game_count": 0}, "games": []}))
    scores = await real_games.measure(path)
    assert set(scores) == set(real_games.SEED_CODES) - m94_protocol.CODES
    assert len(scores) == 11


def test_historical_helper_import_does_not_load_the_m94_freeze(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("synthetic_m94_protocol", m94_protocol.__file__)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    read_bytes = Path.read_bytes

    def missing_manifest(path: Path) -> bytes:
        if path.name == "source_revisions_v2.json":
            raise FileNotFoundError("Synthetic missing M94 freeze")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", missing_manifest)
    spec.loader.exec_module(module)
    assert callable(real_games.reference_labels)
    with pytest.raises(FileNotFoundError):
        module.get_protocol()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["publish", "digest", "incomplete", "missing_candidate"])
async def test_manifest_finalization_is_bounded_and_never_repeats_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path, identity = _synthetic_recording(tmp_path)
    manifest_path = path.with_name("m94_v2_fresh80_manifest.json")
    manifest_path.unlink()
    for name in (
        "recorded.json",
        "fresh80_recorded.json",
        "m79_fresh80_recorded.json",
        "m93_fresh80_recorded.json",
        "m94_fresh80_recorded.json",
    ):
        (tmp_path / name).write_text(json.dumps({"games": []}))
    monkeypatch.setattr(recorder, "DEST", tmp_path)
    monkeypatch.setattr(
        m94_protocol, "validate_frozen_sources", lambda commit, root: m94_protocol.get_protocol()
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Finalization attempted another engine/source read")

    monkeypatch.setattr(recorder, "PooledStockfishAdapter", forbidden)
    monkeypatch.setattr(recorder, "verified_pgn", forbidden)
    recording_digest = identity.recording_sha256
    if failure == "digest":
        path.write_bytes(path.read_bytes() + b" ")
    elif failure == "incomplete":
        corpus = json.loads(path.read_bytes())
        corpus["games"][0]["fast"].pop()
        raw = json.dumps(corpus).encode()
        path.write_bytes(raw)
        recording_digest = hashlib.sha256(raw).hexdigest()
    elif failure == "missing_candidate":
        corpus = json.loads(path.read_bytes())
        corpus["games"][0]["fast"][1][0]["score_cp"] = 300
        raw = json.dumps(corpus).encode()
        path.write_bytes(raw)
        recording_digest = hashlib.sha256(raw).hexdigest()
    else:
        publish = recorder.os.link

        def failed_publication(*args: Any, **kwargs: Any) -> None:
            raise OSError("Synthetic manifest publication failure")

        monkeypatch.setattr(recorder.os, "link", failed_publication)
        with pytest.raises(OSError, match="publication"):
            await recorder.finalize_m94_manifest(
                identity.preregistration_commit, recording_digest, identity.pgn_sha256
            )
        assert not manifest_path.exists()
        assert not list(tmp_path.glob(".m94-manifest-*"))
        monkeypatch.setattr(recorder.os, "link", publish)
        await recorder.finalize_m94_manifest(
            identity.preregistration_commit, recording_digest, identity.pgn_sha256
        )
        assert json.loads(manifest_path.read_bytes()) == _manifest(identity)
        with pytest.raises(ValueError, match="overwrite"):
            await recorder.finalize_m94_manifest(
                identity.preregistration_commit, recording_digest, identity.pgn_sha256
            )
        return
    with pytest.raises(ValueError, match="checksum|incomplete"):
        await recorder.finalize_m94_manifest(
            identity.preregistration_commit, recording_digest, identity.pgn_sha256
        )
    assert not manifest_path.exists()
