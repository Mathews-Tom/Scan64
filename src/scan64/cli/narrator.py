"""``scan64-cli narrator install``: download and register the default narrator model (#429).

The command checks, in order, that the ``ollama`` command exists, that the
local Ollama answers at the fixed address the app uses, and that a GPU has the
narrator's memory budget free. Then it downloads the pinned GGUF, checks its
SHA-256, and runs ``ollama create`` with the benchmark's Modelfile. Each failed
check stops with a message that says what to do; Scan64 then keeps its
template explanations. The command never loads the model.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from scan64.providers.llm.local_ollama import LOCAL_OLLAMA_BASE_URL
from scan64.providers.llm.narrator_model import (
    DEFAULT_NARRATOR_GGUF,
    DEFAULT_NARRATOR_MODEL,
    DEFAULT_NARRATOR_MODEL_NAME,
    NARRATOR_GPU_MEMORY_BUDGET_MIB,
    PinnedGGUF,
    render_modelfile,
)

_PROBE_SECONDS = 4.0
_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
_CHUNK = 1 << 20
_FALLBACK = "Scan64 keeps its template explanations until the model is installed."

Runner = Callable[..., subprocess.CompletedProcess[Any]]


class NarratorInstallError(RuntimeError):
    """A check or step failed; the message tells the player what to do."""


@dataclass(frozen=True)
class GpuMemory:
    name: str
    total_mib: int
    free_mib: int


def add_narrator_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="narrator_command", required=True)
    install = commands.add_parser(
        "install",
        help=f"Download the pinned narrator GGUF and register it as {DEFAULT_NARRATOR_MODEL}.",
    )
    install.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Download directory (default: $SCAN64_DATA_DIR/narrator, else ./data/narrator).",
    )
    install.add_argument(
        "--keep-gguf",
        action="store_true",
        help="Keep the downloaded GGUF after Ollama has copied it.",
    )
    install.add_argument(
        "--skip-gpu-check",
        action="store_true",
        help="Install without reading GPU memory (no NVIDIA GPU, or nvidia-smi missing).",
    )


def narrator_command(args: argparse.Namespace) -> int:
    directory = args.dir or Path(os.environ.get("SCAN64_DATA_DIR", "data")) / "narrator"
    try:
        install_narrator_model(
            directory, keep_gguf=args.keep_gguf, skip_gpu_check=args.skip_gpu_check
        )
    except NarratorInstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def install_narrator_model(
    directory: Path,
    *,
    keep_gguf: bool = False,
    skip_gpu_check: bool = False,
    pin: PinnedGGUF = DEFAULT_NARRATOR_GGUF,
    which: Callable[[str], str | None] | None = None,
    run: Runner | None = None,
    local_transport: httpx.BaseTransport | None = None,
    download_transport: httpx.BaseTransport | None = None,
    out: Callable[[str], None] = print,
) -> None:
    run = run or subprocess.run
    # ``ollama create`` runs inside the directory; resolve it once so the
    # Modelfile path does not resolve a second time from there.
    directory = directory.resolve()
    ollama = (which or shutil.which)("ollama")
    if ollama is None:
        raise NarratorInstallError(
            "Ollama is not installed. Install it from https://ollama.com/download, start it, "
            f"and run this command again. {_FALLBACK}"
        )
    with httpx.Client(transport=local_transport, trust_env=False, timeout=_PROBE_SECONDS) as local:
        version = _ollama_version(local)
        out(f"Ollama {version} at {LOCAL_OLLAMA_BASE_URL}")
        if DEFAULT_NARRATOR_MODEL in _installed(local):
            out(f"{DEFAULT_NARRATOR_MODEL} is already installed.")
            return
        if skip_gpu_check:
            out("GPU memory check skipped.")
        else:
            out(_check_gpu(run))
        gguf = _download(directory, pin, download_transport, out)
        modelfile = directory / f"{DEFAULT_NARRATOR_MODEL_NAME}.Modelfile"
        modelfile.write_text(render_modelfile(f"./{pin.hf_file}"), encoding="utf-8")
        out(f"Registering {DEFAULT_NARRATOR_MODEL} with Ollama")
        created = run(
            [ollama, "create", DEFAULT_NARRATOR_MODEL_NAME, "-f", str(modelfile)],
            cwd=directory,
            env={**os.environ, "OLLAMA_HOST": LOCAL_OLLAMA_BASE_URL.removeprefix("http://")},
            check=False,
        )
        if created.returncode != 0:
            raise NarratorInstallError(
                f"ollama create failed with exit status {created.returncode} (its output is "
                f"above). The narrator was measured with Ollama 0.40.18; update Ollama if it is "
                f"older (this one is {version}). The checked GGUF stays in {directory} for a "
                f"retry. {_FALLBACK}"
            )
        if DEFAULT_NARRATOR_MODEL not in _installed(local):
            raise NarratorInstallError(
                f"ollama create finished, but {LOCAL_OLLAMA_BASE_URL} does not list "
                f"{DEFAULT_NARRATOR_MODEL}. Check that OLLAMA_HOST is unset when Ollama starts."
            )
    if not keep_gguf:
        gguf.unlink()
        modelfile.unlink()
    out(
        f"Installed {DEFAULT_NARRATOR_MODEL}. In Settings, open Local explanation wording, "
        "choose Detect local Ollama, and enable it."
    )


def _ollama_version(client: httpx.Client) -> str:
    try:
        response = client.get(f"{LOCAL_OLLAMA_BASE_URL}/api/version")
        response.raise_for_status()
        version = response.json().get("version")
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        raise NarratorInstallError(
            f"Ollama is installed but does not answer at {LOCAL_OLLAMA_BASE_URL}. "
            f"Start Ollama and run this command again. {_FALLBACK}"
        ) from error
    return version if isinstance(version, str) else "unknown version"


def _installed(client: httpx.Client) -> list[str]:
    try:
        response = client.get(f"{LOCAL_OLLAMA_BASE_URL}/api/tags")
        response.raise_for_status()
        models = response.json().get("models")
    except (httpx.HTTPError, ValueError, AttributeError) as error:
        raise NarratorInstallError(
            f"Ollama at {LOCAL_OLLAMA_BASE_URL} did not list its models."
        ) from error
    if not isinstance(models, list):
        raise NarratorInstallError(f"Ollama at {LOCAL_OLLAMA_BASE_URL} did not list its models.")
    return [entry["name"] for entry in models if isinstance(entry, dict) and "name" in entry]


def gpu_memory(run: Runner = subprocess.run) -> list[GpuMemory] | None:
    """Total and free memory of each NVIDIA GPU, or None without ``nvidia-smi``."""
    try:
        result = run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    gpus: list[GpuMemory] = []
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in line.rsplit(",", 2)]
        if len(fields) != 3:
            continue
        try:
            gpus.append(GpuMemory(fields[0], int(fields[1]), int(fields[2])))
        except ValueError:
            continue
    return gpus or None


def _check_gpu(run: Runner) -> str:
    budget = NARRATOR_GPU_MEMORY_BUDGET_MIB
    gpus = gpu_memory(run)
    if gpus is None:
        raise NarratorInstallError(
            f"Could not read GPU memory (nvidia-smi is missing or failed). The narrator needs "
            f"a GPU with {budget} MiB (4 GiB) free; it does not run on the CPU. If this "
            f"device has such a GPU, run the command again with --skip-gpu-check. {_FALLBACK}"
        )
    best = max(gpus, key=lambda gpu: gpu.free_mib)
    if best.free_mib < budget:
        raise NarratorInstallError(
            f"The GPU has too little free memory: {best.name} has {best.free_mib} of "
            f"{best.total_mib} MiB free, and the narrator needs {budget} MiB (4 GiB). "
            f"Close programs that use the GPU and run this command again. {_FALLBACK}"
        )
    return f"{best.name}: {best.free_mib} MiB free, narrator budget {budget} MiB"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(
    directory: Path,
    pin: PinnedGGUF,
    transport: httpx.BaseTransport | None,
    out: Callable[[str], None],
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / pin.hf_file
    if target.is_file() and _sha256(target) == pin.sha256:
        out(f"Using {target} (SHA-256 checked)")
        return target
    partial = target.with_name(f"{target.name}.part")
    out(f"Downloading {pin.url} ({pin.size_bytes / 2**30:.2f} GiB)")
    digest = hashlib.sha256()
    try:
        with (
            httpx.Client(
                transport=transport, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
            ) as client,
            client.stream("GET", pin.url) as response,
        ):
            response.raise_for_status()
            received = 0
            reported = 0
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(_CHUNK):
                    digest.update(chunk)
                    stream.write(chunk)
                    received += len(chunk)
                    percent = received * 100 // pin.size_bytes
                    if percent >= reported + 10:
                        reported = percent - percent % 10
                        out(f"  {min(reported, 100)}%")
    except httpx.HTTPError as error:
        partial.unlink(missing_ok=True)
        raise NarratorInstallError(
            f"Download failed: {error}. Check the connection and run this command again."
        ) from error
    actual = digest.hexdigest()
    if actual != pin.sha256:
        partial.unlink()
        raise NarratorInstallError(
            f"The download's SHA-256 is {actual}, not the pinned {pin.sha256}. "
            "The file was deleted; nothing was registered."
        )
    partial.replace(target)
    return target
