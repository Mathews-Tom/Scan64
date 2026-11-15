import asyncio
import ctypes
import io
import os
import signal
import sqlite3
import sys
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scan64 import desktop_server


def environment(tmp_path: Path) -> dict[str, str]:
    return {
        "SCAN64_DATA_DIR": str(tmp_path / "desktop data"),
        "SCAN64_PORT": "12345",
        "SCAN64_SHELL_TOKEN": "unit-test-shell-secret",
        "SCAN64_APP_VERSION": "0.6.1",
    }


def test_configuration_uses_shell_directory_port_and_secret(tmp_path: Path) -> None:
    values = environment(tmp_path)
    configuration = desktop_server.configuration_from_environment(values)
    assert configuration.data_dir == (tmp_path / "desktop data").resolve()
    assert configuration.port == 12345
    assert configuration.token == values["SCAN64_SHELL_TOKEN"]
    assert configuration.token not in repr(configuration)
    assert not configuration.data_dir.exists()


@pytest.mark.parametrize("variable", ["SCAN64_DATA_DIR", "SCAN64_PORT", "SCAN64_SHELL_TOKEN"])
def test_required_environment_is_not_optional(tmp_path: Path, variable: str) -> None:
    values = environment(tmp_path)
    del values[variable]
    with pytest.raises(ValueError, match=variable):
        desktop_server.configuration_from_environment(values)


@pytest.mark.parametrize("port", ["", "not-a-port", "0", "-1", "65536"])
def test_invalid_ports_fail_without_disclosing_environment(tmp_path: Path, port: str) -> None:
    values = {**environment(tmp_path), "SCAN64_PORT": port}
    with pytest.raises(ValueError, match="SCAN64_PORT") as error:
        desktop_server.configuration_from_environment(values)
    assert values["SCAN64_SHELL_TOKEN"] not in str(error.value)


@pytest.mark.parametrize("token", ["", " ", "\nsecret", "non-ascii-\u00e9"])
def test_invalid_tokens_fail_without_logging_their_value(tmp_path: Path, token: str) -> None:
    values = {**environment(tmp_path), "SCAN64_SHELL_TOKEN": token}
    with pytest.raises(ValueError, match="SCAN64_SHELL_TOKEN"):
        desktop_server.configuration_from_environment(values)


@pytest.mark.parametrize("already_exists", [False, True])
def test_environment_is_configured_before_database_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already_exists: bool
) -> None:
    values = environment(tmp_path)
    for name in [*values, "SCAN64_DATABASE_URL", "SCAN64_DISTRIBUTION"]:
        monkeypatch.setenv(name, "inherited-value")
    configuration = desktop_server.configuration_from_environment(values)
    if already_exists:
        configuration.data_dir.mkdir()
        if sys.platform != "win32":
            configuration.data_dir.chmod(0o755)
    desktop_server.configure_environment(configuration)
    assert configuration.data_dir.is_dir()
    if sys.platform != "win32":
        assert configuration.data_dir.stat().st_mode & 0o777 == 0o700
    database_path = (configuration.data_dir / "scan64.db").as_posix()
    assert os.environ["SCAN64_DATABASE_URL"] == f"sqlite:///{database_path}"
    assert os.environ["SCAN64_DATA_DIR"] == str(configuration.data_dir)
    assert os.environ["SCAN64_SHELL_TOKEN"] == values["SCAN64_SHELL_TOKEN"]
    assert os.environ["SCAN64_DISTRIBUTION"] == "desktop"
    assert not (configuration.data_dir / "scan64.db").exists()


@pytest.mark.parametrize("contents", [b"", b"bytes-before-parent-closes-stdin"])
def test_eof_arms_independent_deadline_before_requesting_graceful_stop(
    contents: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []
    timer = Mock()
    timer.start.side_effect = lambda: events.append("timer-started")
    timer_factory = Mock(return_value=timer)
    monkeypatch.setattr(desktop_server.threading, "Timer", timer_factory)
    stop = Mock()
    stop.set.side_effect = lambda: events.append("stop-requested")
    desktop_server._watch_parent(io.BytesIO(contents), stop)
    timer_factory.assert_called_once_with(
        desktop_server.PARENT_EXIT_TIMEOUT, desktop_server._exit_process_tree, args=(0,)
    )
    assert 0 < desktop_server.PARENT_EXIT_TIMEOUT < 5
    assert timer.daemon is True
    assert events == ["timer-started", "stop-requested"]


@pytest.mark.parametrize("error", [OSError("broken pipe"), ValueError("closed stream")])
def test_broken_parent_pipe_is_also_a_shutdown_request(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    timer = Mock()
    monkeypatch.setattr(desktop_server.threading, "Timer", Mock(return_value=timer))
    stream = Mock()
    stream.read.side_effect = error
    stop = threading.Event()
    desktop_server._watch_parent(stream, stop)
    assert stop.is_set()
    timer.start.assert_called_once()


@pytest.mark.parametrize("already_isolated", [False, True])
def test_posix_isolates_server_before_any_engine_can_spawn(
    already_isolated: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_os = SimpleNamespace(
        name="posix",
        getpid=lambda: 123,
        getpgrp=lambda: 123 if already_isolated else 456,
        setsid=Mock(),
    )
    monkeypatch.setattr(desktop_server, "os", fake_os)
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(desktop_server, "_process_group", None)
    desktop_server._configure_process_cleanup()
    assert desktop_server._process_group == 123
    assert fake_os.setsid.call_count == (0 if already_isolated else 1)


def test_windows_requires_a_successfully_assigned_job(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_server, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(desktop_server, "_windows_job", None)
    create_job = Mock(return_value=789)
    monkeypatch.setattr(desktop_server, "_create_windows_job", create_job)
    desktop_server._configure_process_cleanup()
    assert desktop_server._windows_job == 789
    create_job.side_effect = OSError("job assignment failed")
    with pytest.raises(OSError, match="job assignment failed"):
        desktop_server._configure_process_cleanup()


class ProcessExited(BaseException):
    pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX group signals do not exist on Windows")
@pytest.mark.parametrize("isolated", [False, True])
def test_forced_posix_exit_never_signals_the_shells_group(
    isolated: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_os = SimpleNamespace(
        name="posix",
        getpid=lambda: 123,
        killpg=Mock(),
        _exit=Mock(side_effect=ProcessExited),
    )
    monkeypatch.setattr(desktop_server, "os", fake_os)
    monkeypatch.setattr(desktop_server, "_process_group", 123 if isolated else None)
    with pytest.raises(ProcessExited):
        desktop_server._exit_process_tree(0)
    if isolated:
        fake_os.killpg.assert_called_once_with(123, signal.SIGKILL)
    else:
        fake_os.killpg.assert_not_called()
    fake_os._exit.assert_called_once_with(0)


def test_windows_forced_exit_relies_on_kernel_closing_the_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_os = SimpleNamespace(name="nt", _exit=Mock(side_effect=ProcessExited))
    monkeypatch.setattr(desktop_server, "os", fake_os)
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(platform="win32"))
    with pytest.raises(ProcessExited):
        desktop_server._exit_process_tree(0)
    fake_os._exit.assert_called_once_with(0)


@pytest.mark.parametrize("failure", [None, "create", "configure", "assign"])
def test_windows_job_has_kill_on_close_and_fails_closed(
    failure: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(platform="win32"))
    kernel = SimpleNamespace(
        CreateJobObjectW=Mock(return_value=0 if failure == "create" else 789),
        SetInformationJobObject=Mock(return_value=failure != "configure"),
        GetCurrentProcess=Mock(return_value=123),
        AssignProcessToJobObject=Mock(return_value=failure != "assign"),
        CloseHandle=Mock(return_value=True),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(
        ctypes, "WinError", lambda code: OSError(code, "Windows Job error"), raising=False
    )
    if failure is not None:
        with pytest.raises(OSError, match="Windows Job error"):
            desktop_server._create_windows_job()
        if failure == "create":
            kernel.CloseHandle.assert_not_called()
        else:
            kernel.CloseHandle.assert_called_once_with(789)
    else:
        assert desktop_server._create_windows_job() == 789
        kernel.CreateJobObjectW.assert_called_once_with(None, None)
        information_call = kernel.SetInformationJobObject.call_args.args
        assert information_call[0:2] == (789, 9)
        assert information_call[2]._obj.BasicLimitInformation.LimitFlags == 0x00002000
        kernel.AssignProcessToJobObject.assert_called_once_with(789, 123)
        kernel.CloseHandle.assert_not_called()


@pytest.mark.parametrize("windowed", [False, True])
def test_main_configures_only_loopback_with_no_access_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windowed: bool
) -> None:
    values = environment(tmp_path)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SCAN64_DATABASE_URL", "sqlite:///inherited.db")
    monkeypatch.setenv("SCAN64_DISTRIBUTION", "source")
    monkeypatch.setattr(desktop_server, "_configure_process_cleanup", Mock())
    exit_tree = Mock(side_effect=ProcessExited)
    monkeypatch.setattr(desktop_server, "_exit_process_tree", exit_tree)
    threads = Mock()
    monkeypatch.setattr(desktop_server.threading, "Thread", threads)
    parent_pipe = io.BytesIO()
    recover_pipe = Mock(return_value=parent_pipe)
    monkeypatch.setattr(desktop_server, "_open_windows_parent_pipe", recover_pipe)
    fake_sys = SimpleNamespace(
        stdin=None if windowed else io.TextIOWrapper(parent_pipe),
        stdout=None if windowed else sys.stdout,
        stderr=None if windowed else sys.stderr,
        platform="win32" if windowed else sys.platform,
    )
    monkeypatch.setattr(desktop_server, "sys", fake_sys)
    configuration = Mock(return_value="uvicorn-configuration")
    server = SimpleNamespace(started=True, run=Mock())

    class FakeServer:
        def __init__(self, config: object) -> None:
            server_factory(config)
            self.started = False

        async def startup(self, sockets: object = None) -> None:
            self.started = True

        def run(self) -> None:
            asyncio.run(self.startup())
            server.run()

    server_factory = Mock()

    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(Config=configuration, Server=FakeServer),
    )
    with pytest.raises(ProcessExited):
        desktop_server.main()
    configuration.assert_called_once_with(
        "scan64.api.app:app",
        host="127.0.0.1",
        port=12345,
        access_log=False,
        timeout_graceful_shutdown=2,
    )
    server_factory.assert_called_once_with("uvicorn-configuration")
    server.run.assert_called_once()
    exit_tree.assert_called_once_with(0)
    assert os.environ["SCAN64_DATABASE_URL"].endswith("/desktop data/scan64.db")
    assert threads.call_args_list[0].kwargs["args"][0] is parent_pipe
    assert (tmp_path / "desktop data" / "desktop-app-version").read_text() == "0.6.1\n"
    if windowed:
        recover_pipe.assert_called_once()
        assert fake_sys.stdout is fake_sys.stderr
        fake_sys.stderr.close()
        assert (tmp_path / "desktop data" / "logs" / "server.log").is_file()
    else:
        recover_pipe.assert_not_called()


def test_missing_shell_pipe_is_a_fatal_startup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_server, "_configure_process_cleanup", Mock())
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(stdin=None, platform="linux"))
    diagnostic = Mock()
    monkeypatch.setattr(desktop_server.traceback, "print_exc", diagnostic)
    exit_tree = Mock(side_effect=ProcessExited)
    monkeypatch.setattr(desktop_server, "_exit_process_tree", exit_tree)
    with pytest.raises(ProcessExited):
        desktop_server.main()
    diagnostic.assert_called_once()
    exit_tree.assert_called_once_with(1)


@pytest.mark.parametrize("terminal", [False, True])
def test_parent_stream_rejects_missing_or_terminal_input(
    terminal: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdin = SimpleNamespace(isatty=lambda: True) if terminal else None
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(stdin=stdin, platform="linux"))
    with pytest.raises(RuntimeError, match="requires a piped stdin"):
        desktop_server._parent_stream()


@pytest.mark.parametrize(
    ("handle", "file_type"),
    [(None, 3), (ctypes.c_void_p(-1).value, 3), (456, 0), (456, 1), (456, 2)],
)
def test_windowed_stdin_requires_a_real_inherited_pipe(
    handle: int | None, file_type: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(platform="win32"))
    kernel = SimpleNamespace(
        GetStdHandle=Mock(return_value=handle), GetFileType=Mock(return_value=file_type)
    )
    descriptor = Mock()
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=descriptor))
    with pytest.raises(RuntimeError, match="requires a piped stdin"):
        desktop_server._open_windows_parent_pipe()
    kernel.GetStdHandle.assert_called_once_with(-10)
    descriptor.assert_not_called()


@pytest.mark.parametrize("opening_fails", [False, True])
def test_windowed_stdin_recovers_binary_non_inheritable_handle_without_console(
    opening_fails: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = SimpleNamespace(GetStdHandle=Mock(return_value=456), GetFileType=Mock(return_value=3))
    descriptor = Mock(return_value=17)
    monkeypatch.setattr(desktop_server, "sys", SimpleNamespace(platform="win32"))
    pipe = io.BytesIO()
    fake_os = SimpleNamespace(
        O_RDONLY=0,
        O_BINARY=0x8000,
        set_inheritable=Mock(),
        fdopen=(
            Mock(side_effect=OSError("fdopen failed")) if opening_fails else Mock(return_value=pipe)
        ),
        close=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=kernel), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(open_osfhandle=descriptor))
    monkeypatch.setattr(desktop_server, "os", fake_os)
    if opening_fails:
        with pytest.raises(OSError, match="fdopen failed"):
            desktop_server._open_windows_parent_pipe()
        fake_os.close.assert_called_once_with(17)
    else:
        assert desktop_server._open_windows_parent_pipe() is pipe
        fake_os.close.assert_not_called()
    descriptor.assert_called_once_with(456, 0x8000)
    fake_os.set_inheritable.assert_called_once_with(17, False)
    fake_os.fdopen.assert_called_once_with(17, "rb", buffering=0)


@pytest.mark.parametrize("failure", [None, "backup", "migration", "bind", "marker"])
def test_version_backup_precedes_api_loading_and_migrations_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    from scan64 import desktop_backups

    data_dir = tmp_path / "desktop data"
    data_dir.mkdir()
    marker = data_dir / desktop_backups.VERSION_MARKER
    marker.write_text("0.6.1\n", encoding="utf-8")
    with closing(sqlite3.connect(data_dir / "scan64.db")) as database:
        database.execute("CREATE TABLE migration_log (version TEXT)")
        database.execute("INSERT INTO migration_log VALUES ('0.6.1')")
        database.commit()
    for name, value in {**environment(tmp_path), "SCAN64_APP_VERSION": "0.6.2"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SCAN64_DATABASE_URL", "sqlite:///inherited.db")
    monkeypatch.setenv("SCAN64_DISTRIBUTION", "source")
    monkeypatch.setattr(desktop_server, "_configure_process_cleanup", Mock())
    monkeypatch.setattr(desktop_server, "_parent_stream", lambda: io.BytesIO())
    monkeypatch.setattr(desktop_server.threading, "Thread", Mock())
    exit_tree = Mock(side_effect=ProcessExited)
    monkeypatch.setattr(desktop_server, "_exit_process_tree", exit_tree)
    monkeypatch.setattr(desktop_server.traceback, "print_exc", Mock())
    if failure == "backup":
        monkeypatch.setattr(
            desktop_backups, "_backup_database", Mock(side_effect=OSError("backup failed"))
        )
    if failure == "marker":
        monkeypatch.setattr(
            desktop_backups.DesktopBackup,
            "record_successful_startup",
            Mock(side_effect=OSError("marker failed")),
        )
    events: list[str] = []

    def load_api(*args: object, **kwargs: object) -> object:
        snapshots = list((data_dir / "backups").glob(desktop_backups.BACKUP_PATTERN))
        assert len(snapshots) == 1
        with closing(sqlite3.connect(snapshots[0])) as snapshot:
            assert snapshot.execute("SELECT version FROM migration_log").fetchall() == [("0.6.1",)]
        events.append("api-loaded")
        return object()

    class FakeServer:
        def __init__(self, configuration: object) -> None:
            self.started = False

        async def startup(self, sockets: object = None) -> None:
            events.append("migration")
            assert marker.read_text() == "0.6.1\n"
            if failure == "migration":
                raise RuntimeError("migration failed")
            with closing(sqlite3.connect(data_dir / "scan64.db")) as database:
                database.execute("INSERT INTO migration_log VALUES ('0.6.2')")
                database.commit()
            self.started = failure != "bind"

        def run(self) -> None:
            asyncio.run(self.startup())
            if self.started:
                assert marker.read_text() == "0.6.2\n"

    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(Config=load_api, Server=FakeServer))
    with pytest.raises(ProcessExited):
        desktop_server.main()
    assert events == ([] if failure == "backup" else ["api-loaded", "migration"])
    exit_tree.assert_called_once_with(0 if failure is None else 1)
    assert marker.read_text() == ("0.6.2\n" if failure is None else "0.6.1\n")
