"""The prompt structures of runs 1-9 and the local Ollama client that runs them.

Runs 1-5 ask for a whole comment in one call. Runs 6-9 ask for one sentence per fact: code checks
each sentence, retries it twice, falls back to the plain fact, and joins the sentences.
Each record carries ``meta`` (commit, dirty tree, model digest, Ollama version, options, prompt
version), so a result can be tied to the code and the model that made it (guardrail 6).
"""

import json
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmarks.narrator.voice import data
from benchmarks.narrator.voice.check import colour_words, finish, repair, sentence_ok
from benchmarks.narrator.voice.render import render

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "scan64-bench-gemma4-e4b-q3km:latest"
NUM_CTX = 4096
Record = dict[str, Any]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def _get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read())


@dataclass
class Client:
    """One model on one Ollama server, with fixed sampling options."""

    url: str = DEFAULT_URL
    model: str = DEFAULT_MODEL
    options: dict[str, float | int] = field(default_factory=dict)

    def chat(self, system: str, user: str) -> tuple[str, float, int, int]:
        """Reply text, seconds, prompt tokens and output tokens. Thinking is always off."""
        body = json.dumps(
            {
                "model": self.model,
                "think": False,
                "stream": False,
                "options": {"num_ctx": NUM_CTX, **self.options},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        ).encode()
        start = time.monotonic()
        request = urllib.request.Request(
            self.url + "/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            reply = json.loads(response.read())
        return (
            reply["message"]["content"].strip(),
            time.monotonic() - start,
            reply.get("prompt_eval_count", 0),
            reply.get("eval_count", 0),
        )

    def meta(self, prompt_version: str) -> Record:
        """What a record needs to be reproduced: code, model, server and options."""
        tags = _get(self.url + "/api/tags")["models"]
        names = {self.model, self.model + ":latest"}
        digest = next((m["digest"] for m in tags if m["name"] in names), None)
        if digest is None:
            raise SystemExit(f"model {self.model!r} is not on the Ollama server at {self.url}")
        return {
            "commit": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
            "model": self.model,
            "model_digest": digest,
            "ollama_version": _get(self.url + "/api/version")["version"],
            "think": False,
            "options": {"num_ctx": NUM_CTX, **self.options},
            "prompt_version": prompt_version,
        }


# Runs 1-3: whole comment ---------------------------------------------------------------------


def facts_block_v1(facts: list[str], roles: list[str] | None) -> str:
    if roles:
        return "Facts:\n" + "\n".join(f"- {r}: {f}" for r, f in zip(roles, facts, strict=True))
    return "Facts:\n" + "\n".join(f"- {f}" for f in facts)


def prompt_v1(variant: str, style: str | None, test: str, board: bool, use_roles: bool) -> str:
    """Variants: plain, name, traits, traits+examples, examples."""
    parts = []
    if variant in ("name", "traits", "traits+examples"):
        parts.append(f"Style: {style}")
    if style and variant in ("traits", "traits+examples"):
        parts.append(f"How this style writes: {data.STYLES_V1[style]['traits']}")
    if style and variant in ("traits+examples", "examples"):
        if variant == "examples":
            parts.append("Write in the manner of these examples.")
        for ex_facts, ex_roles, text in zip(
            data.EXAMPLE_FACTS_V1,
            data.EXAMPLE_ROLES_V1,
            data.STYLES_V1[style]["examples"],
            strict=True,
        ):
            block = facts_block_v1(ex_facts, ex_roles if use_roles else None)
            parts.append(f"Example\n{block}\nComment: {text}")
    position = f"Position:\n{render(data.FENS[test])}\n" if board else ""
    block = facts_block_v1(data.TESTS_V1[test], data.ROLES_V1[test] if use_roles else None)
    parts.append(f"Now write the comment.\n{position}{block}\nComment:")
    return "\n\n".join(parts)


V1_RUNS = {
    1: (("name", "traits", "traits+examples", "examples"), False, False),
    2: (("name", "traits+examples", "examples"), True, False),
    3: (("name", "traits+examples", "examples"), True, True),
}


def run_v1(
    client: Client, run: int, samples: int, styles: list[str], only: list[str] | None
) -> Iterator[Record]:
    variants, board, use_roles = V1_RUNS[run]
    suffix = ("+board" if board else "") + ("+roles" if use_roles else "")
    cells: list[tuple[str, str | None]] = [("plain", None)]
    cells += [(v, s) for s in styles for v in variants]
    for test in only or data.TESTS_V1:
        for variant, style in cells:
            user = prompt_v1(variant, style, test, board, use_roles)
            for n in range(samples):
                text, seconds, _, _ = client.chat(data.SYSTEM_V1, user)
                yield {
                    "test": test,
                    "variant": variant + suffix,
                    "style": style,
                    "n": n,
                    "text": text,
                    "seconds": round(seconds, 2),
                    "prompt_chars": len(user),
                }


# Runs 4-5: whole comment, facts by role --------------------------------------------------------


def facts_block_roles(facts: list[tuple[str, str]]) -> str:
    return "Facts:\n" + "\n".join(f"- {role}: {text}" for role, text in facts)


def prompt_roles(run: int, style: str, test: str) -> str:
    card = data.STYLES_RUN4[style]
    if run == 4:
        example_facts, examples, tests = data.EXAMPLE_FACTS_RUN4, card["examples"], data.TESTS_RUN4
    else:
        example_facts, examples = data.EXAMPLE_FACTS_RUN5, data.EXAMPLES_RUN5[style]
        tests = data.TESTS_RUN5
    parts = [f"Style: {style}", f"How this style writes: {card['traits']}"]
    for facts, text in zip(example_facts, examples, strict=True):
        parts.append(f"Example\n{facts_block_roles(facts)}\nComment: {text}")
    parts.append(
        f"Now write the comment.\nPosition:\n{render(data.FENS[test])}\n"
        f"{facts_block_roles(tests[test])}\nComment:"
    )
    return "\n\n".join(parts)


def run_roles(
    client: Client, run: int, samples: int, styles: list[str], only: list[str] | None
) -> Iterator[Record]:
    system = data.SYSTEM_RUN4 if run == 4 else data.SYSTEM_RUN5
    tests = data.TESTS_RUN4 if run == 4 else data.TESTS_RUN5
    for test in only or tests:
        for style in styles:
            user = prompt_roles(run, style, test)
            for n in range(samples):
                raw, seconds, _, _ = client.chat(system, user)
                row: Record = {"test": test, "variant": f"run{run}", "style": style, "n": n}
                if run == 4:
                    row |= {"text": raw}
                else:
                    text, repaired = repair(raw, data.PLAYED[test])
                    row |= {"text": text, "raw": raw, "repaired": repaired}
                yield row | {"seconds": round(seconds, 2), "prompt_chars": len(user)}


# Runs 6-9: one fact per call ------------------------------------------------------------------


def sentences_for(run: int, style: str) -> list[list[str]]:
    if run == 7 and style == "Chernev":
        return data.SENTENCES_CHERNEV_RUN7
    return data.SENTENCES_RUN6[style]


def prompt_fact(run: int, style: str, role: str, fact: str, so_far: list[str]) -> str:
    """The style card, every example fact with its example sentence, then the fact to rewrite."""
    parts = [
        f"Style: {style}",
        f"How this style writes: {data.CARDS_RUN6[style]}",
        "Examples:",
    ]
    for facts, sentences in zip(data.EXAMPLE_FACTS_RUN5, sentences_for(run, style), strict=True):
        for (ex_role, ex_fact), sentence in zip(facts, sentences, strict=True):
            parts.append(f"Fact ({ex_role}): {ex_fact}\nSentence: {sentence}")
    context = f"Comment so far: {' '.join(so_far)}\n" if run == 7 and so_far else ""
    parts.append(f"Now rewrite this fact.\n{context}Fact ({role}): {fact}\nSentence:")
    return "\n\n".join(parts)


def first_line(raw: str) -> str:
    return raw.strip().splitlines()[0] if raw.strip() else ""


def comment_per_fact(
    client: Client, run: int, style: str, facts: list[tuple[str, str]], played: str, system: str
) -> Record:
    """One comment: a checked sentence per fact, joined by code."""
    sentences: list[str] = []
    used: set[str] = set()
    calls = fallbacks = rewrites = out_tokens = 0
    seconds = 0.0
    prompt_tokens: list[int] = []
    for role, fact in facts:
        base = prompt_fact(run, style, role, fact, sentences)
        avoid: set[str] = set()
        chosen: str | None = None
        for _ in range(3):
            user = base
            if avoid:
                user = base.replace(
                    "Now rewrite this fact.",
                    f"Now rewrite this fact. Do not use: {', '.join(sorted(avoid))}.",
                )
            raw, s, p_tok, o_tok = client.chat(system, user)
            calls += 1
            seconds += s
            prompt_tokens.append(p_tok)
            out_tokens += o_tok
            text, _ = repair(first_line(raw), played)
            if not text or sentence_ok(text, fact):
                continue
            if run >= 8:
                repeated = colour_words(text, fact) & used
                if repeated and not avoid:
                    avoid = repeated
                    rewrites += 1
                    chosen = chosen or text
                    continue
            chosen = text
            break
        if chosen is None:
            chosen = fact
            fallbacks += 1
        sentences.append(chosen if run == 6 else finish(chosen))
        used |= colour_words(chosen, fact)
    record: Record = {
        "text": " ".join(sentences),
        "calls": calls,
        "fallback_sentences": fallbacks,
        "sentences": len(facts),
        "seconds": round(seconds, 2),
    }
    if run >= 7:
        record |= {"prompt_tokens_max": max(prompt_tokens), "output_tokens": out_tokens}
    if run >= 8:
        record["rewrites"] = rewrites
    return record


def run_per_fact(
    client: Client,
    run: int,
    samples: int,
    styles: list[str],
    only: list[str] | None,
    own_words: bool = False,
) -> Iterator[Record]:
    if run == 9:
        tests, played = data.TESTS_HELDOUT, data.PLAYED_HELDOUT
        system = data.SYSTEM_RUN6 + (data.OWN_WORDS_RULE if own_words else "")
    else:
        tests, played = data.TESTS_RUN5, data.PLAYED
        system = data.SYSTEM_RUN7 if run == 7 else data.SYSTEM_RUN6
    for test in only or tests:
        facts = tests[test]
        for style in styles:
            for n in range(samples):
                record = comment_per_fact(client, run, style, facts, played[test], system)
                head: Record = {"test": test, "variant": f"run{run}", "style": style, "n": n}
                if run == 9:
                    head |= {"model": client.model, "own": own_words, "options": client.options}
                yield head | record


# Runs ---------------------------------------------------------------------------------------

DEFAULT_SAMPLES = {1: 3, 2: 3, 3: 3, 4: 10, 5: 10, 6: 10, 7: 20, 8: 20, 9: 10}
DEFAULT_STYLES = {
    1: list(data.STYLES_V1),
    2: list(data.STYLES_V1),
    3: list(data.STYLES_V1),
    4: list(data.STYLES_RUN4),
    5: list(data.STYLES_RUN4),
    6: list(data.CARDS_RUN6),
    7: ["Tal"],
    8: ["Tal"],
    9: ["Tal"],
}


def run(
    client: Client,
    number: int,
    samples: int | None = None,
    styles: list[str] | None = None,
    own_words: bool = False,
    tests: list[str] | None = None,
) -> Iterator[Record]:
    """Records of one run; each carries ``meta``. ``tests`` limits the positions (a smoke run)."""
    known = data.TESTS_HELDOUT if number == 9 else data.FENS
    unknown = sorted(set(tests or []) - set(known))
    if unknown:
        raise SystemExit(f"run {number} has no position {', '.join(unknown)}")
    meta = client.meta(f"run{number}" + ("+own" if own_words else ""))
    samples = samples or DEFAULT_SAMPLES[number]
    styles = styles or DEFAULT_STYLES[number]
    if number <= 3:
        records = run_v1(client, number, samples, styles, tests)
    elif number <= 5:
        records = run_roles(client, number, samples, styles, tests)
    else:
        records = run_per_fact(client, number, samples, styles, tests, own_words)
    for record in records:
        yield record | {"meta": meta}
