import os
import socket
from pathlib import Path

import pytest

from scan64.desktop import InstanceLock, configure_environment, data_directory, reserve_port


def test_data_directory_is_independent_of_working_directory(tmp_path: Path) -> None:
    assert data_directory(tmp_path) == tmp_path / "Library/Application Support/Scan64"


def test_environment_uses_bundle_and_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PATH", raising=False)
    for key in ("SCAN64_DATABASE_URL", "SCAN64_DATA_DIR", "SCAN64_WEB_DIST"):
        monkeypatch.setenv(key, "wrong")
    data = data_directory(tmp_path)
    resources = tmp_path / "bundle"
    configure_environment(resources, data)
    assert data.is_dir()
    assert os.environ["SCAN64_DATABASE_URL"] == f"sqlite:///{data / 'scan64.db'}"
    assert os.environ["SCAN64_DATA_DIR"] == str(data)
    assert os.environ["SCAN64_WEB_DIST"] == str(resources / "web")
    assert os.environ["PATH"] == f"{resources / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin"


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
