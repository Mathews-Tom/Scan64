"""``scan64-cli narrator install`` with doubles for Ollama, nvidia-smi and the download (#429)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from scan64.cli import narrator
from scan64.cli.narrator import NarratorInstallError, gpu_memory, install_narrator_model
from scan64.providers.llm.narrator_model import (
    DEFAULT_NARRATOR_MODEL,
    DEFAULT_NARRATOR_MODEL_NAME,
    PinnedGGUF,
)

WEIGHTS = b"GGUF test weights" * 1024
PIN = PinnedGGUF(
    hf_repo="example/narrator-GGUF",
    hf_revision="0" * 40,
    hf_file="narrator.gguf",
    sha256=hashlib.sha256(WEIGHTS).hexdigest(),
    size_bytes=len(WEIGHTS),
    licence="Apache-2.0",
    licence_url="https://example.invalid/licence",
)


class Ollama:
    """The local daemon: /api/version and /api/tags; ``ollama create`` adds the model."""

    def __init__(self, *, installed: tuple[str, ...] = (), running: bool = True) -> None:
        self.models = list(installed)
        self.running = running
        self.paths: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "127.0.0.1" and request.url.port == 11434
            self.paths.append(request.url.path)
            if not self.running:
                raise httpx.ConnectError("refused", request=request)
            if request.url.path == "/api/version":
                return httpx.Response(200, json={"version": "0.40.18"})
            assert request.url.path == "/api/tags"
            return httpx.Response(200, json={"models": [{"name": name} for name in self.models]})

        return httpx.MockTransport(handler)


class Commands:
    """``nvidia-smi`` and ``ollama create`` doubles; records every command."""

    def __init__(self, ollama: Ollama, *, gpus: str | None, create_status: int = 0) -> None:
        self.ollama = ollama
        self.gpus = gpus
        self.create_status = create_status
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.modelfile = ""

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        if argv[0] == "nvidia-smi":
            if self.gpus is None:
                raise FileNotFoundError("nvidia-smi")
            return subprocess.CompletedProcess(argv, 0, stdout=self.gpus, stderr="")
        assert argv[:3] == ["/usr/bin/ollama", "create", DEFAULT_NARRATOR_MODEL_NAME]
        modelfile = Path(argv[4])
        self.modelfile = modelfile.read_text(encoding="utf-8")
        assert (modelfile.parent / PIN.hf_file).read_bytes() == WEIGHTS
        if self.create_status == 0:
            self.ollama.models.append(DEFAULT_NARRATOR_MODEL)
        return subprocess.CompletedProcess(argv, self.create_status)


def download(body: bytes = WEIGHTS) -> tuple[httpx.MockTransport, list[str]]:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, content=body)

    return httpx.MockTransport(handler), urls


def install(
    tmp_path: Path,
    ollama: Ollama,
    commands: Commands,
    body: bytes = WEIGHTS,
    *,
    which: str | None = "/usr/bin/ollama",
    **options: Any,
) -> tuple[list[str], list[str]]:
    transport, urls = download(body)
    lines: list[str] = []
    install_narrator_model(
        tmp_path / "narrator",
        pin=PIN,
        which=lambda _name: which,
        run=commands,
        local_transport=ollama.transport(),
        download_transport=transport,
        out=lines.append,
        **options,
    )
    return lines, urls


ONE_GPU = "NVIDIA GeForce RTX 4060, 8188, 7300\n"


def test_install_downloads_checks_the_digest_and_registers_the_model(tmp_path: Path) -> None:
    ollama = Ollama(installed=("other:latest",))
    commands = Commands(ollama, gpus=ONE_GPU)
    lines, urls = install(tmp_path, ollama, commands)
    assert urls == [PIN.url]
    argv, kwargs = commands.calls[-1]
    assert kwargs["cwd"] == tmp_path / "narrator"
    assert kwargs["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert "FROM ./narrator.gguf\n" in commands.modelfile
    assert "PARAMETER num_ctx 4096\n" in commands.modelfile
    assert ollama.models == ["other:latest", DEFAULT_NARRATOR_MODEL]
    # Ollama keeps its own copy; the download is removed unless kept.
    assert list((tmp_path / "narrator").iterdir()) == []
    assert lines[-1].startswith(f"Installed {DEFAULT_NARRATOR_MODEL}.")


def test_a_relative_directory_names_the_modelfile_from_inside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default ``data/narrator`` is relative; ``ollama create`` runs inside it."""
    monkeypatch.chdir(tmp_path)
    ollama = Ollama()
    commands = Commands(ollama, gpus=ONE_GPU)
    transport, _ = download()
    install_narrator_model(
        Path("data/narrator"),
        pin=PIN,
        which=lambda _name: "/usr/bin/ollama",
        run=commands,
        local_transport=ollama.transport(),
        download_transport=transport,
        out=lambda _line: None,
    )
    argv, kwargs = commands.calls[-1]
    assert (Path(kwargs["cwd"]) / argv[4]) == tmp_path / "data/narrator" / Path(argv[4]).name
    assert "FROM ./narrator.gguf\n" in commands.modelfile
    assert DEFAULT_NARRATOR_MODEL in ollama.models


def test_a_kept_gguf_is_reused_without_a_second_download(tmp_path: Path) -> None:
    ollama = Ollama()
    install(tmp_path, ollama, Commands(ollama, gpus=ONE_GPU), keep_gguf=True)
    assert (tmp_path / "narrator" / PIN.hf_file).read_bytes() == WEIGHTS
    ollama.models.clear()
    _, urls = install(tmp_path, ollama, Commands(ollama, gpus=ONE_GPU), keep_gguf=True)
    assert urls == []


def test_a_wrong_digest_registers_nothing(tmp_path: Path) -> None:
    ollama = Ollama()
    commands = Commands(ollama, gpus=ONE_GPU)
    with pytest.raises(NarratorInstallError, match="SHA-256 .* not the pinned"):
        install(tmp_path, ollama, commands, body=WEIGHTS + b"tampered")
    assert [argv[0] for argv, _ in commands.calls] == ["nvidia-smi"]
    assert list((tmp_path / "narrator").iterdir()) == []


def test_missing_ollama_stops_before_any_request(tmp_path: Path) -> None:
    ollama = Ollama()
    commands = Commands(ollama, gpus=ONE_GPU)
    with pytest.raises(NarratorInstallError, match="Ollama is not installed.*template"):
        install(tmp_path, ollama, commands, which=None)
    assert ollama.paths == [] and commands.calls == []


def test_a_stopped_ollama_is_named(tmp_path: Path) -> None:
    ollama = Ollama(running=False)
    commands = Commands(ollama, gpus=ONE_GPU)
    with pytest.raises(NarratorInstallError, match="does not answer at http://127.0.0.1:11434"):
        install(tmp_path, ollama, commands)
    assert commands.calls == []


@pytest.mark.parametrize(
    ("gpus", "message"),
    [
        ("NVIDIA GeForce GTX 1650, 4096, 3600\n", "too little free memory.*3600 of 4096 MiB"),
        (None, "Could not read GPU memory.*--skip-gpu-check"),
    ],
)
def test_too_little_or_unknown_gpu_memory_downloads_nothing(
    tmp_path: Path, gpus: str | None, message: str
) -> None:
    ollama = Ollama()
    commands = Commands(ollama, gpus=gpus)
    transport, urls = download()
    with pytest.raises(NarratorInstallError, match=message):
        install_narrator_model(
            tmp_path,
            pin=PIN,
            which=lambda _name: "/usr/bin/ollama",
            run=commands,
            local_transport=ollama.transport(),
            download_transport=transport,
            out=lambda _line: None,
        )
    assert urls == []
    assert DEFAULT_NARRATOR_MODEL not in ollama.models


def test_skip_gpu_check_installs_without_nvidia_smi(tmp_path: Path) -> None:
    ollama = Ollama()
    commands = Commands(ollama, gpus=None)
    install(tmp_path, ollama, commands, skip_gpu_check=True)
    assert [argv[0] for argv, _ in commands.calls] == ["/usr/bin/ollama"]
    assert DEFAULT_NARRATOR_MODEL in ollama.models


def test_an_installed_model_is_left_alone(tmp_path: Path) -> None:
    ollama = Ollama(installed=(DEFAULT_NARRATOR_MODEL,))
    commands = Commands(ollama, gpus=ONE_GPU)
    lines, urls = install(tmp_path, ollama, commands)
    assert urls == [] and commands.calls == []
    assert lines[-1] == f"{DEFAULT_NARRATOR_MODEL} is already installed."


def test_a_failed_create_names_the_ollama_version(tmp_path: Path) -> None:
    ollama = Ollama()
    with pytest.raises(NarratorInstallError, match="create failed with exit status 1.*0.40.18"):
        install(tmp_path, ollama, Commands(ollama, gpus=ONE_GPU, create_status=1))


def test_gpu_memory_reads_every_gpu_and_the_most_free_one_decides(tmp_path: Path) -> None:
    ollama = Ollama()
    two = "NVIDIA A, 2048, 1000\nNVIDIA B, Ti, 12282, 11000\n"
    gpus = gpu_memory(Commands(ollama, gpus=two))
    assert gpus is not None
    free = [(gpu.name, gpu.free_mib) for gpu in gpus]
    assert free == [("NVIDIA A", 1000), ("NVIDIA B, Ti", 11000)]
    assert narrator._check_gpu(Commands(ollama, gpus=two)).startswith("NVIDIA B, Ti: 11000")


def test_the_cli_reports_a_failure_on_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(narrator.shutil, "which", lambda _name: None)
    monkeypatch.setattr("sys.argv", ["scan64-cli", "narrator", "install", "--dir", str(tmp_path)])
    from scan64.cli.main import main

    with pytest.raises(SystemExit) as exited:
        main()
    assert exited.value.code == 1
    assert "Ollama is not installed" in capsys.readouterr().err
