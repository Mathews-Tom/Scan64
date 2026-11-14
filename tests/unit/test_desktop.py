import os
import socket
import sys
from pathlib import Path

import pytest

import scan64.desktop as desktop
from scan64.desktop import InstanceLock, configure_environment, data_directory, reserve_port


def test_data_directory_is_independent_of_working_directory(tmp_path: Path) -> None:
    assert data_directory(tmp_path) == tmp_path / "Library/Application Support/Scan64"


def test_environment_uses_bundle_and_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PATH", raising=False)
    for key in ("SCAN64_DATABASE_URL", "SCAN64_DATA_DIR", "SCAN64_WEB_DIST", "SCAN64_DISTRIBUTION"):
        monkeypatch.setenv(key, "wrong")
    data = data_directory(tmp_path)
    resources = tmp_path / "bundle"
    (resources / "bin").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    configure_environment(resources, data)
    assert data.is_dir()
    assert os.environ["SCAN64_DATABASE_URL"] == f"sqlite:///{data / 'scan64.db'}"
    assert os.environ["SCAN64_DATA_DIR"] == str(data)
    assert os.environ["SCAN64_WEB_DIST"] == str(resources / "web")
    assert os.environ["SCAN64_DISTRIBUTION"] == "desktop"
    assert os.environ["PATH"] == f"{resources / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"
    assert Path.cwd() == resources / "bin"


def test_reserved_port_skips_occupied_port() -> None:
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        port = occupied.getsockname()[1]
        with reserve_port(port) as listener:
            assert listener.getsockname()[1] > port
            with socket.socket() as contender, pytest.raises(OSError):
                contender.bind(listener.getsockname())


def test_single_instance_and_stale_lock_recovery(tmp_path: Path) -> None:
    primary = InstanceLock(tmp_path)
    secondary = InstanceLock(tmp_path)
    try:
        assert primary.acquire()
        primary.publish(8002)
        assert not secondary.acquire()
        assert secondary.existing_url() == "http://127.0.0.1:8002"
        secondary.close()
        primary.close()
        replacement = InstanceLock(tmp_path)
        try:
            assert replacement.acquire()
        finally:
            replacement.close()
    finally:
        for instance in (primary, secondary):
            if not instance.file.closed:
                instance.close()


def test_instance_metadata_cannot_open_arbitrary_url(tmp_path: Path) -> None:
    instance = InstanceLock(tmp_path)
    try:
        instance.file.write("https://example.com")
        instance.file.flush()
        with pytest.raises(RuntimeError, match="did not finish"):
            instance.existing_url(timeout=0.01)
    finally:
        instance.close()


def test_instance_lock_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "unrelated"
    target.write_text("keep")
    (tmp_path / "instance.lock").symlink_to(target)
    with pytest.raises(OSError):
        InstanceLock(tmp_path)
    assert target.read_text() == "keep"


def test_port_can_be_reused_immediately_after_connection_shutdown() -> None:
    with reserve_port() as listener:
        port = listener.getsockname()[1]
        listener.listen()
        with socket.create_connection(("127.0.0.1", port)) as client:
            connection, _ = listener.accept()
            with connection:
                connection.shutdown(socket.SHUT_RDWR)
            assert client.recv(1) == b""
    with reserve_port(port) as replacement:
        assert replacement.getsockname()[1] == port


def test_second_launch_opens_existing_instance_without_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = tmp_path / "bundle"
    (resources / "bin").mkdir(parents=True)
    data = data_directory(tmp_path)
    data.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", resources, raising=False)
    monkeypatch.setattr(desktop, "data_directory", lambda: data)
    for key in (
        "PATH",
        "SCAN64_DATABASE_URL",
        "SCAN64_DATA_DIR",
        "SCAN64_WEB_DIST",
        "SCAN64_DISTRIBUTION",
    ):
        monkeypatch.setenv(key, os.environ.get(key, ""))
    opened: list[str] = []

    def open_browser(url: str) -> bool:
        opened.append(url)
        return True

    def unexpected_server() -> socket.socket:
        pytest.fail("A second launch must not reserve another server socket")

    monkeypatch.setattr(desktop.webbrowser, "open", open_browser)
    monkeypatch.setattr(desktop, "reserve_port", unexpected_server)
    primary = InstanceLock(data)
    try:
        assert primary.acquire()
        primary.publish(8009)
        original_stdout, original_stderr = sys.stdout, sys.stderr
        desktop.main()
        assert opened == ["http://127.0.0.1:8009"]
        assert (sys.stdout, sys.stderr) == (original_stdout, original_stderr)
    finally:
        primary.close()
