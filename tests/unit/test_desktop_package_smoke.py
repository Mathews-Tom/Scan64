"""Bundle acceptance must inspect installed files, not just the staging directory."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/desktop-package-smoke.py"
spec = importlib.util.spec_from_file_location("desktop_package_smoke", SCRIPT)
assert spec is not None and spec.loader is not None
packaging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packaging)


def test_installed_resource_root_is_discovered_from_engine_notices(tmp_path: Path) -> None:
    resources = tmp_path / "usr/lib/Scan64"
    engine = resources / "stockfish"
    engine.mkdir(parents=True)
    (engine / "Copying.txt").write_text("GPL")
    assert packaging.resource_root(tmp_path) == resources


def test_ambiguous_installed_resource_trees_are_rejected(tmp_path: Path) -> None:
    for name in ("stale", "current"):
        engine = tmp_path / name / "stockfish"
        engine.mkdir(parents=True)
        (engine / "Copying.txt").write_text("GPL")
    with pytest.raises(AssertionError, match="Expected one installed resource tree"):
        packaging.resource_root(tmp_path)


def test_bundle_content_check_rejects_missing_runtime(tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="Missing bundled file: scan64-server/"):
        packaging.check_contents(tmp_path)


def test_bundle_content_check_requires_font_and_rust_notices(tmp_path: Path) -> None:
    executable = ".exe" if packaging.os.name == "nt" else ""
    paths = [
        f"scan64-server/scan64-server{executable}",
        f"stockfish/stockfish{executable}",
        "stockfish/stockfish-sf_18.tar.gz",
        "stockfish/Copying.txt",
        "stockfish/nn-c288c895ea92.nnue",
        "stockfish/nn-37f18f62d772.nnue",
        "notices/LICENSE",
        "notices/THIRD_PARTY_NOTICES.md",
        "notices/WEB_THIRD_PARTY_LICENSES.md",
    ]
    for name in paths:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    with pytest.raises(AssertionError, match="Missing bundled file: notices/rust-licenses.html"):
        packaging.check_contents(tmp_path)
    for name in ("notices/rust-licenses.html", "notices/rust-licenses.json"):
        (tmp_path / name).write_text("fixture")
    with pytest.raises(
        AssertionError, match="Missing bundled file: notices/fonts/host-grotesk-OFL"
    ):
        packaging.check_contents(tmp_path)


@pytest.mark.parametrize("subsystem", [2, 3])
def test_packaged_windows_executables_cannot_allocate_console(
    tmp_path: Path, subsystem: int
) -> None:
    binary = tmp_path / "stockfish.exe"
    content = bytearray(512)
    content[:2] = b"MZ"
    content[0x3C:0x40] = (128).to_bytes(4, "little")
    content[128:132] = bytes([0x50, 0x45, 0, 0])
    content[220:222] = subsystem.to_bytes(2, "little")
    binary.write_bytes(content)
    if subsystem == 2:
        packaging.check_windowless(binary)
    else:
        with pytest.raises(AssertionError, match="Console subsystem"):
            packaging.check_windowless(binary)


def test_process_executable_identifies_the_running_interpreter() -> None:
    helpers = packaging.smoke_helpers()
    executable = helpers.process_executable(packaging.os.getpid())
    assert executable is not None
    assert executable.samefile(helpers.sys.executable)


@pytest.mark.parametrize("name", ["stockfish", "stockfish.exe", "STOCKFISH.EXE"])
def test_engine_snapshot_does_not_count_webview_helpers(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    helpers = packaging.smoke_helpers()

    def children(parent: int) -> list[int]:
        assert parent == 10
        return [11, 12, 13]

    executables = {11: Path("/bundle/stockfish") / name, 12: Path("/bundle/WebKitWebProcess")}
    monkeypatch.setattr(helpers, "descendants", children)
    monkeypatch.setattr(helpers, "process_executable", executables.get)
    assert helpers.stockfish_children(10) == [11]


def test_failed_app_smoke_preserves_both_logs_before_temporary_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helpers = packaging.smoke_helpers()
    server_log = tmp_path / "data" / "logs" / "server.log"
    server_log.parent.mkdir(parents=True)
    server_log.write_text("server failure")
    evidence = tmp_path / "evidence"

    class Exited:
        returncode = 17

        def poll(self) -> int:
            return 17

    def spawn(*args, **kwargs):
        kwargs["stdout"].write(b"app failure")
        return Exited()

    monkeypatch.setattr(helpers.subprocess, "Popen", spawn)
    with pytest.raises(AssertionError, match="Shell exited before smoke record"):
        helpers.run(Path("/unused"), tmp_path, kill=True, failure_logs=evidence)
    assert (evidence / "server.log").read_text() == "server failure"
    assert (evidence / "app.log").read_text() == "app failure"
