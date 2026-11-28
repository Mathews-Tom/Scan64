"""The default local narrator model (#462): gemma-4-E4B-it at Q4_K_S.

Chosen in #462 for the voice pipeline of #454
(docs/evaluation/narrator-models.md, "Voice pipeline decision"). It replaces
Q3_K_M, the #429 default chosen in #389 for the slot prompt. Scan64 does not ship the
weights: ``scan64-cli narrator install`` downloads this exact GGUF, checks its
SHA-256 and registers it with the local Ollama under ``DEFAULT_NARRATOR_MODEL``.
The pin and the Modelfile are the ones the benchmark ran
(``benchmarks/narrator/narrator.toml``, ``benchmarks/narrator/modelfiles``).
"""

from __future__ import annotations

from dataclasses import dataclass

from scan64.providers.llm.adapters import DEFAULT_OLLAMA_CONTEXT_TOKENS


@dataclass(frozen=True)
class PinnedGGUF:
    """One GGUF file pinned by repository revision and content digest."""

    hf_repo: str
    hf_revision: str
    hf_file: str
    sha256: str
    size_bytes: int
    licence: str
    licence_url: str

    @property
    def url(self) -> str:
        return f"https://huggingface.co/{self.hf_repo}/resolve/{self.hf_revision}/{self.hf_file}"


# The benchmarked file (narrator.toml entry ``gemma4-e4b-q4ks``). Its GGUF
# metadata records ``general.license = apache-2.0`` and the Gemma 4 licence link.
DEFAULT_NARRATOR_GGUF = PinnedGGUF(
    hf_repo="unsloth/gemma-4-E4B-it-GGUF",
    hf_revision="bfc15c382204943c3a8fff0c750b94ae2364d7a3",
    hf_file="gemma-4-E4B-it-Q4_K_S.gguf",
    sha256="0fdbd6df44e505481a0636fe3160d230d6de2ecf0f8a20f06a5c64adef0cae00",
    size_bytes=4_844_850_304,
    licence="Apache-2.0",
    licence_url="https://ai.google.dev/gemma/docs/gemma_4_license",
)

# Name registered with ``ollama create``; ``/api/tags`` lists it with ``:latest``.
DEFAULT_NARRATOR_MODEL_NAME = "scan64-narrator-gemma4-e4b-q4ks"
DEFAULT_NARRATOR_MODEL = f"{DEFAULT_NARRATOR_MODEL_NAME}:latest"

# GPU memory budget of the narrator. #462 measured 2.84 GiB for this model at
# num_ctx 4096 on an RTX 5090.
NARRATOR_GPU_MEMORY_BUDGET_MIB = 4096
NARRATOR_NUM_PREDICT = 2048

# benchmarks/narrator/modelfiles/gemma4-e4b-q4ks.Modelfile, verbatim; a unit
# test keeps the two identical.
MODELFILE_TEMPLATE = """\
# gemma-4-E4B-it at Q4_K_S (unsloth/gemma-4-E4B-it-GGUF, pinned revision), text only.
# Chat format and sampling copied from the Ollama library model gemma4:e4b
# (`ollama show gemma4:e4b --modelfile`, Ollama 0.40.18). The library model also
# loads a vision projector and an MTP draft model; this one loads neither.
FROM @GGUF@
TEMPLATE {{ .Prompt }}
RENDERER gemma4
PARSER gemma4
PARAMETER temperature 1
PARAMETER top_k 64
PARAMETER top_p 0.95
PARAMETER num_ctx @NUM_CTX@
PARAMETER num_predict @NUM_PREDICT@
"""


def render_modelfile(source: str, *, num_ctx: int = DEFAULT_OLLAMA_CONTEXT_TOKENS) -> str:
    """The Modelfile for the GGUF at ``source`` with the benchmark's context and output cap.

    ``ollama create`` resolves a relative ``FROM`` path against the Modelfile's
    directory, so a Modelfile written next to the GGUF can name it as ``./<file>``
    and the path never holds the spaces of a home directory.
    """
    return (
        MODELFILE_TEMPLATE.replace("@GGUF@", source)
        .replace("@NUM_CTX@", str(num_ctx))
        .replace("@NUM_PREDICT@", str(NARRATOR_NUM_PREDICT))
    )
