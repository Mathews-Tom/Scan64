#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==13.0.0", "numpy==2.5.5"]
# ///
"""Build the matte material board textures listed in ``scripts/board-textures.toml``.

Downloads each CC0 albedo map once into the cache directory, verifies its SHA-256, and composes
one full-board image per board: every square takes its own crop and rotation of the source, so no
tile repeats. Each crop is scaled in linear light to the board's target colour, keeping the natural
grain or veining. Writes WebP and AVIF, the CSS that selects them, and a manifest with the encoded
sizes and the measured light/dark luminance ratio. Fails when a board exceeds the byte budget or
falls below the contrast minimum.

Run: ``uv run scripts/build-board-textures.py`` (``--print-hashes`` reports source hashes only).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import tomllib
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "scripts" / "board-textures.toml"
CSS_OUTPUT = ROOT / "apps" / "scan64-web" / "src" / "components" / "boardMaterials.generated.css"
NOTICES = ROOT / "THIRD_PARTY_NOTICES.md"
PUBLIC_URL_PREFIX = "/boards"
USER_AGENT = "scan64-board-texture-build"
SQUARES = 8

Float = NDArray[np.float64]
log = logging.getLogger("build-board-textures")


@dataclass(frozen=True)
class Side:
    source: str
    crop: int
    tint: tuple[float, float, float]
    contrast: float
    rotations: tuple[int, ...]


def parse_hex(value: str) -> tuple[float, float, float]:
    if len(value) != 7 or not value.startswith("#"):
        raise ValueError(f"tint must be #rrggbb, got {value!r}")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def to_hex(rgb: Float) -> str:
    return "#" + "".join(f"{round(float(c) * 255):02x}" for c in rgb)


def srgb_to_linear(c: Float) -> Float:
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c: Float) -> Float:
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def relative_luminance(srgb: Float) -> Float:
    lin = srgb_to_linear(srgb)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def fetch_source(name: str, spec: dict[str, object], cache: Path) -> Path:
    target = cache / f"{name}.jpg"
    if not target.exists():
        url = str(spec["url"])
        log.info("downloading %s from %s", name, url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
        member = spec.get("zip_member")
        if member is not None:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                payload = archive.read(str(member))
        cache.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    return target


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source(name: str, spec: dict[str, object], cache: Path) -> Float:
    path = fetch_source(name, spec, cache)
    actual = sha256(path)
    expected = str(spec["sha256"])
    if actual != expected:
        raise RuntimeError(f"{name}: SHA-256 {actual} does not match configured {expected!r}")
    licence = spec["licence"]
    if licence != "CC0-1.0":
        raise RuntimeError(f"{name}: licence {licence!r} is not CC0-1.0")
    image = Image.open(path).convert("RGB")
    rotate = int(str(spec["grain_rotate"]))
    if rotate:
        image = image.rotate(rotate, expand=True)
    return np.asarray(image, dtype=np.float64) / 255


def parse_side(raw: dict[str, object]) -> Side:
    rotations = tuple(int(r) for r in raw["rotations"])  # type: ignore[attr-defined]
    if not rotations or any(r % 90 for r in rotations):
        raise ValueError(f"rotations must be multiples of 90, got {rotations}")
    return Side(
        source=str(raw["source"]),
        crop=int(str(raw["crop"])),
        tint=parse_hex(str(raw["tint"])),
        contrast=float(str(raw["contrast"])),
        rotations=rotations,
    )


def square_texture(source: Float, side: Side, square_px: int, rng: np.random.Generator) -> Float:
    if side.crop < square_px:
        raise ValueError(f"{side.source}: crop {side.crop} px would upscale to {square_px} px")
    height, width, _ = source.shape
    if side.crop > min(height, width):
        raise ValueError(f"{side.source}: crop {side.crop} px exceeds the {width}x{height} source")
    y = int(rng.integers(0, height - side.crop + 1))
    x = int(rng.integers(0, width - side.crop + 1))
    crop = source[y : y + side.crop, x : x + side.crop]
    turns = int(rng.choice(side.rotations)) // 90
    crop = np.rot90(crop, k=turns)
    resized = Image.fromarray(np.round(crop * 255).astype(np.uint8)).resize(
        (square_px, square_px), Image.Resampling.LANCZOS
    )
    linear = srgb_to_linear(np.asarray(resized, dtype=np.float64) / 255)
    mean = linear.reshape(-1, 3).mean(axis=0)
    target = srgb_to_linear(np.array(side.tint))
    # Relative variation around the crop mean keeps the grain; the gain moves its mean to the tint.
    variation = (linear / mean - 1.0) * side.contrast
    return linear_to_srgb(target * (1.0 + variation))


def light_mask(board_px: int) -> NDArray[np.bool_]:
    index = np.arange(board_px) * SQUARES // board_px
    # a8 (top left from White's side) is a light square.
    return (index[:, None] + index[None, :]) % 2 == 0


def compose(
    board: dict[str, object], sources: dict[str, Float], board_px: int, rng: np.random.Generator
) -> Float:
    square_px = board_px // SQUARES
    light = parse_side(board["light"])  # type: ignore[arg-type]
    dark = parse_side(board["dark"])  # type: ignore[arg-type]
    canvas = np.zeros((board_px, board_px, 3))
    for row in range(SQUARES):
        for col in range(SQUARES):
            side = light if (row + col) % 2 == 0 else dark
            tile = square_texture(sources[side.source], side, square_px, rng)
            canvas[
                row * square_px : (row + 1) * square_px, col * square_px : (col + 1) * square_px
            ] = tile
    return canvas


def measure(path: Path, board_px: int) -> dict[str, object]:
    with Image.open(path) as encoded:
        decoded = np.asarray(encoded.convert("RGB"), dtype=np.float64) / 255
    if decoded.shape[:2] != (board_px, board_px):
        raise RuntimeError(f"{path.name}: decoded size {decoded.shape[:2]} is not {board_px}")
    mask = light_mask(board_px)
    luminance = relative_luminance(decoded)
    light_l = float(luminance[mask].mean())
    dark_l = float(luminance[~mask].mean())
    return {
        "light_mean": to_hex(decoded[mask].mean(axis=0)),
        "dark_mean": to_hex(decoded[~mask].mean(axis=0)),
        "light_luminance": round(light_l, 4),
        "dark_luminance": round(dark_l, 4),
        "luminance_ratio": round((light_l + 0.05) / (dark_l + 0.05), 2),
    }


# Safari added type() inside image-set() only in 17. On Safari 14-16 an image-set() with
# type() makes background-image invalid at computed-value time, which also drops the
# checkerboard. Every board therefore sets a plain WebP url first and upgrades to the
# AVIF/WebP image-set() only where the browser parses type().
IMAGE_SET_SUPPORTS = "@supports (background-image: image-set(url('probe.avif') type('image/avif')))"


def css_for(entries: list[dict[str, object]]) -> str:
    lines = [
        "/* Generated by scripts/build-board-textures.py from scripts/board-textures.toml.",
        "   Do not edit. Matte albedo-only textures (CC0); see THIRD_PARTY_NOTICES.md.",
        "   The flat checkerboard uses the measured mean colours until the texture loads.",
        "   Safari 14-16 cannot parse type() in image-set(), so the base rules use WebP",
        "   and the @supports block upgrades to AVIF where type() parses. */",
    ]
    upgrades = []
    for entry in entries:
        board_id = entry["id"]
        files = entry["files"]
        selector = f":root[data-board-theme='{board_id}']"
        lines.append(
            f"{selector} {{ --board-light: {entry['light_mean']}; "
            f"--board-dark: {entry['dark_mean']}; "
            f"--board-texture: url('{files['webp']['url']}'); }}"  # type: ignore[index]
        )
        upgrades.append(
            f"  {selector} {{ --board-texture: image-set("
            f"url('{files['avif']['url']}') type('image/avif'), "  # type: ignore[index]
            f"url('{files['webp']['url']}') type('image/webp')); }}"  # type: ignore[index]
        )
    lines += [f"{IMAGE_SET_SUPPORTS} {{", *upgrades, "}"]
    return "\n".join(lines) + "\n"


def notice_for(source_specs: dict[str, dict[str, object]], used: set[str]) -> str:
    lines = [
        "Scan64 material board textures",
        "",
        "Each board image combines crops of the albedo (colour) maps below, scaled to the",
        "board's colours by scripts/build-board-textures.py. All sources are dedicated to the",
        "public domain under CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/",
        "",
    ]
    for name in sorted(used):
        spec = source_specs[name]
        lines += [
            f"{spec['title']} ({spec['author']}), {spec['licence']}",
            f"  Page: {spec['page']}",
            f"  File: {spec['url']}",
            f"  SHA-256 of the colour map: {spec['sha256']}",
            "",
        ]
    return "\n".join(lines)


def check_notices(source_specs: dict[str, dict[str, object]], used: set[str]) -> None:
    notices = NOTICES.read_text(encoding="utf-8")
    missing = [name for name in sorted(used) if str(source_specs[name]["page"]) not in notices]
    if missing:
        raise RuntimeError(f"THIRD_PARTY_NOTICES.md does not list texture sources: {missing}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument(
        "--print-hashes", action="store_true", help="download sources and print SHA-256"
    )
    args = parser.parse_args()

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "build" / "board-textures"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / f"build-board-textures_{stamp}.log"),
        ],
    )

    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    output = config["output"]
    cache = ROOT / output["cache_dir"]
    source_specs: dict[str, dict[str, object]] = config["sources"]

    if args.print_hashes:
        for name, spec in source_specs.items():
            print(f"{name} {sha256(fetch_source(name, spec, cache))}")
        return 0

    board_px = int(output["board_px"])
    if board_px % SQUARES:
        raise ValueError(f"board_px {board_px} is not a multiple of {SQUARES}")
    out_dir = ROOT / output["dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    budget = int(output["budget_bytes_per_board"])
    min_ratio = float(output["min_luminance_ratio"])

    used = {parse_side(b[k]).source for b in config["boards"] for k in ("light", "dark")}
    unknown = used - source_specs.keys()
    if unknown:
        raise KeyError(f"boards reference undefined sources: {sorted(unknown)}")
    check_notices(source_specs, used)
    sources = {name: load_source(name, source_specs[name], cache) for name in sorted(used)}

    entries: list[dict[str, object]] = []
    failures: list[str] = []
    for index, board in enumerate(config["boards"]):
        board_id = str(board["id"])
        if board["group"] not in ("wood", "stone"):
            raise ValueError(f"{board_id}: group must be wood or stone")
        rng = np.random.default_rng([int(output["seed"]), index])
        image = Image.fromarray(
            np.round(compose(board, sources, board_px, rng) * 255).astype(np.uint8)
        )
        webp = out_dir / f"{board_id}.webp"
        avif = out_dir / f"{board_id}.avif"
        quality = {
            **{"webp": output["webp_quality"], "avif": output["avif_quality"]},
            **board.get("quality", {}),
        }
        image.save(webp, "WEBP", quality=int(quality["webp"]), method=6)
        image.save(avif, "AVIF", quality=int(quality["avif"]), speed=2)
        measured = {fmt: measure(path, board_px) for fmt, path in (("webp", webp), ("avif", avif))}
        files = {
            fmt: {"url": f"{PUBLIC_URL_PREFIX}/{path.name}", "bytes": path.stat().st_size}
            for fmt, path in (("webp", webp), ("avif", avif))
        }
        ratio = min(float(str(m["luminance_ratio"])) for m in measured.values())
        largest = max(int(f["bytes"]) for f in files.values())
        sides = {k: board[k]["source"] for k in ("light", "dark")}
        entry = {
            "id": board_id,
            "label": board["label"],
            "group": board["group"],
            "sources": sides,
            "files": files,
            **measured["webp"],
            "luminance_ratio": ratio,
            "max_texture_bytes": largest,
        }
        entries.append(entry)
        log.info(
            "%s: webp %d B, avif %d B, luminance ratio %.2f",
            board_id,
            files["webp"]["bytes"],
            files["avif"]["bytes"],
            ratio,
        )
        if largest > budget:
            failures.append(f"{board_id}: {largest} B exceeds the {budget} B budget")
        if ratio < min_ratio:
            failures.append(f"{board_id}: luminance ratio {ratio} is below {min_ratio}")

    manifest = {
        "board_px": board_px,
        "budget_bytes_per_board": budget,
        "min_luminance_ratio": min_ratio,
        "boards": entries,
    }
    manifest_text = json.dumps(manifest, indent=2) + "\n"
    (ROOT / output["manifest"]).write_text(manifest_text, encoding="utf-8", newline="\n")
    CSS_OUTPUT.write_text(css_for(entries), encoding="utf-8", newline="\n")
    notice = notice_for(source_specs, used)
    (out_dir / "NOTICE.txt").write_text(notice, encoding="utf-8", newline="\n")
    if failures:
        raise RuntimeError("; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
