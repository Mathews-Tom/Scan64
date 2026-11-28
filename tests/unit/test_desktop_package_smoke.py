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
        f"caissa/caissa{executable}",
        f"arasan/arasan{executable}",
        "stockfish/stockfish-sf_18.tar.gz",
        "stockfish/Copying.txt",
        "stockfish/nn-c288c895ea92.nnue",
        "stockfish/nn-37f18f62d772.nnue",
        "caissa/caissa-2.0.tar.gz",
        "caissa/eval-ml-8-152B-spsa.pnn",
        "caissa/LICENSE",
        "caissa/Fathom-LICENSE",
        "caissa/Caissa-Nets-LICENSE",
        "arasan/arasan-v26.1.tar.gz",
        "arasan/fathom-c9c6fef0.tar.gz",
        "arasan/arasanv8-20261005.nnue",
        "arasan/LICENSE",
        "arasan/Fathom-LICENSE",
        "engines/caissa.toml",
        "engines/arasan.toml",
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


def test_bundle_content_check_requires_both_permissive_engines(tmp_path: Path) -> None:
    executable = ".exe" if packaging.os.name == "nt" else ""
    for name in (f"scan64-server/scan64-server{executable}", f"stockfish/stockfish{executable}"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("fixture")
    with pytest.raises(AssertionError, match=f"Missing bundled file: caissa/caissa{executable}"):
        packaging.check_contents(tmp_path)


def test_bundled_engine_files_must_match_the_dockerfile_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = ".exe" if packaging.os.name == "nt" else ""
    resources = tmp_path / "resources"
    names = [f"scan64-server/scan64-server{executable}", "notices/LICENSE"]
    names += [f"{folder}/{stem}{executable}" for folder, stem, _ in packaging.ENGINES]
    names += ["stockfish/Copying.txt", "caissa/LICENSE", "caissa/Fathom-LICENSE"]
    names += ["arasan/LICENSE", "arasan/Fathom-LICENSE", "notices/THIRD_PARTY_NOTICES.md"]
    names += ["notices/WEB_THIRD_PARTY_LICENSES.md", "notices/rust-licenses.html"]
    names += ["notices/rust-licenses.json", "notices/fonts/host-grotesk-OFL.txt"]
    names += ["notices/fonts/jetbrains-mono-OFL.txt", "engines/caissa.toml", "engines/arasan.toml"]
    pinned = [
        "stockfish/stockfish-sf_18.tar.gz",
        "stockfish/nn-c288c895ea92.nnue",
        "stockfish/nn-37f18f62d772.nnue",
        "caissa/caissa-2.0.tar.gz",
        "caissa/eval-ml-8-152B-spsa.pnn",
        "caissa/Caissa-Nets-LICENSE",
        "arasan/arasan-v26.1.tar.gz",
        "arasan/fathom-c9c6fef0.tar.gz",
        "arasan/arasanv8-20261005.nnue",
    ]
    for name in names + pinned:
        (resources / name).parent.mkdir(parents=True, exist_ok=True)
        (resources / name).write_bytes(b"fixture")
    digest = packaging.hashlib.sha256(b"fixture").hexdigest()
    lines = [f"echo '{digest}  /out/{Path(name).name}' | sha256sum --check" for name in pinned]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text("\n".join(lines) + "\n")
    monkeypatch.setattr(packaging, "ROOT", checkout)
    monkeypatch.setattr(packaging, "check_windowless", lambda binary: None)
    assert len(packaging.check_contents(resources)) == len(names) + len(pinned)
    (resources / "caissa/eval-ml-8-152B-spsa.pnn").write_bytes(b"other network")
    with pytest.raises(AssertionError, match="eval-ml-8-152B-spsa.pnn"):
        packaging.check_contents(resources)
    (resources / "caissa/eval-ml-8-152B-spsa.pnn").write_bytes(b"fixture")
    (checkout / "Dockerfile").write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(AssertionError, match="Missing authoritative digest: arasanv8"):
        packaging.check_contents(resources)


def uci_script(tmp_path: Path, name: str, *, ready: bool, exit_code: int = 0) -> Path:
    script = tmp_path / f"engine-{len(list(tmp_path.iterdir()))}"
    reply = "readyok" if ready else "info string failed to load network, terminating"
    script.write_text(
        f"#!{packaging.sys.executable}\n"
        "import sys\n"
        "for line in sys.stdin:\n"
        "    if line.strip() == 'uci':\n"
        f"        print('id name {name}')\n"
        "        print('uciok', flush=True)\n"
        "    elif line.strip() == 'isready':\n"
        f"        print({reply!r}, flush=True)\n"
        f"        {'' if ready else 'sys.exit(1)'}\n"
        "    elif line.strip() == 'quit':\n"
        "        break\n"
        f"sys.exit({exit_code})\n"
    )
    script.chmod(0o755)
    return script


@pytest.mark.skipif(packaging.os.name == "nt", reason="The fixture engine is a POSIX script")
def test_engine_handshake_requires_identity_uciok_and_readyok(tmp_path: Path) -> None:
    engine = uci_script(tmp_path, "Caissa 2.0 POPCNT", ready=True)
    assert packaging.check_engine_handshake(engine, "Caissa 2.0", tmp_path) == "Caissa 2.0 POPCNT"
    with pytest.raises(AssertionError, match="not Arasan 26.1"):
        packaging.check_engine_handshake(engine, "Arasan 26.1", tmp_path)
    missing_network = uci_script(tmp_path, "Arasan 26.1", ready=False)
    with pytest.raises(AssertionError, match="did not answer isready"):
        packaging.check_engine_handshake(missing_network, "Arasan 26.1", tmp_path)
    # The name matches up to a word boundary: an architecture suffix, not a later version.
    neon = uci_script(tmp_path, "Caissa 2.0 ARM NEON", ready=True)
    assert packaging.check_engine_handshake(neon, "Caissa 2.0", tmp_path) == "Caissa 2.0 ARM NEON"
    later = uci_script(tmp_path, "Caissa 2.00", ready=True)
    with pytest.raises(AssertionError, match="not Caissa 2.0"):
        packaging.check_engine_handshake(later, "Caissa 2.0", tmp_path)
    failed = uci_script(tmp_path, "Caissa 2.0 ARM NEON", ready=True, exit_code=7)
    with pytest.raises(AssertionError, match="exited 7"):
        packaging.check_engine_handshake(failed, "Caissa 2.0", tmp_path)


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
    # On Windows a venv python.exe is a launcher that starts the base interpreter.
    interpreters = {helpers.sys.executable, getattr(helpers.sys, "_base_executable", None)}
    # A macOS framework build (python.org, Homebrew, actions/setup-python) runs
    # the Python.app binary inside the framework, so that is the process image.
    if getattr(helpers.sys, "_framework", None):
        interpreters.add(
            packaging.os.path.join(
                helpers.sys.base_prefix, "Resources", "Python.app", "Contents", "MacOS", "Python"
            )
        )
    assert any(executable.samefile(path) for path in interpreters if path)


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


@pytest.mark.skipif(packaging.os.name == "nt", reason="Unix installers use symlink launchers")
def test_smoke_cli_canonicalizes_bundle_parents_but_preserves_apprun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helpers = packaging.smoke_helpers()
    actual = tmp_path / "bundle"
    actual.mkdir()
    inner = actual / "scan64-desktop"
    inner.write_text("executable")
    (actual / "AppRun").symlink_to(inner)
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    monkeypatch.setattr(
        helpers.sys,
        "argv",
        ["desktop-smoke.py", str(alias / "AppRun"), str(tmp_path / "result.json")],
    )

    def record(binary: Path, directory: Path, **kwargs) -> dict:
        assert binary == actual.resolve() / "AppRun"
        assert binary.is_symlink()
        return {}

    monkeypatch.setattr(helpers, "run", record)
    helpers.main()


def test_windows_ancestry_rejects_stale_recycled_parent_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helpers = packaging.smoke_helpers()

    class Windows:
        name = "nt"

    records = [
        {"ProcessId": 50, "ParentProcessId": 1, "Started": 200},
        {"ProcessId": 51, "ParentProcessId": 50, "Started": 201},
        {"ProcessId": 52, "ParentProcessId": 50, "Started": 100},
        {"ProcessId": 53, "ParentProcessId": 51, "Started": 202},
    ]

    def snapshot(command, **kwargs):
        assert "CreationDate" in command[-1]
        return helpers.subprocess.CompletedProcess(command, 0, stdout=helpers.json.dumps(records))

    monkeypatch.setattr(helpers, "os", Windows)
    monkeypatch.setattr(helpers.subprocess, "run", snapshot)
    assert helpers.descendants(50) == [51, 53]
