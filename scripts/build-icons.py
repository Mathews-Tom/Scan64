#!/usr/bin/env python3
"""Regenerate every Scan64 icon from the two source SVGs.

``icons/source/icon-large.svg`` (the knight's tour on a board) is used above 32 px;
``icons/source/icon-small.svg`` (the tour alone) is used at 32 px and below.
Requires macOS ``iconutil`` and ``rsvg-convert`` (``brew install librsvg``).
Run ``python3 scripts/build-icons.py``
and commit the regenerated files.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "apps" / "scan64-desktop" / "src-tauri" / "icons"
PUBLIC = ROOT / "apps" / "scan64-web" / "public"
SITE = ROOT / "site"
LARGE = ICONS / "source" / "icon-large.svg"
SMALL = ICONS / "source" / "icon-small.svg"
SMALL_MAX = 32
TILE = '<rect width="180" height="180" rx="40" fill="#232323"/>'
INSET_VIEWBOX = 'viewBox="-12.857 -12.857 205.714 205.714"'

ICONSET = [
    ("icon_16x16", 16),
    ("icon_16x16@2x", 32),
    ("icon_32x32", 32),
    ("icon_32x32@2x", 64),
    ("icon_128x128", 128),
    ("icon_128x128@2x", 256),
    ("icon_256x256", 256),
    ("icon_256x256@2x", 512),
    ("icon_512x512", 512),
    ("icon_512x512@2x", 1024),
]
ICO_SIZES = [16, 24, 32, 48, 64, 256]


def source_for(size: int) -> Path:
    return SMALL if size <= SMALL_MAX else LARGE


def render(svg: Path, size: int, output: Path) -> None:
    subprocess.run(
        [
            "rsvg-convert",
            "--width",
            str(size),
            "--height",
            str(size),
            "--output",
            str(output),
            str(svg),
        ],
        check=True,
    )


def full_bleed(work: Path, name: str, view_box: str) -> Path:
    """Large artwork on an unrounded ink square, for platforms that apply their own mask."""
    text = LARGE.read_text(encoding="utf-8")
    if INSET_VIEWBOX not in text or TILE not in text:
        raise SystemExit("icon-large.svg no longer has the expected tile; update build-icons.py")
    text = text.replace(INSET_VIEWBOX, f'viewBox="{view_box}"').replace(
        TILE, '<rect x="-100" y="-100" width="380" height="380" fill="#232323"/>'
    )
    path = work / name
    path.write_text(text, encoding="utf-8")
    return path


def write_ico(frames: list[tuple[int, bytes]], output: Path) -> None:
    """Windows ICO with PNG-compressed frames (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries = b""
    for size, png in frames:
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    output.write_bytes(header + entries + b"".join(png for _, png in frames))


def main() -> None:
    for tool in ("rsvg-convert", "iconutil"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is required; see the module docstring")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        iconset = work / "icon.iconset"
        iconset.mkdir()
        for name, size in ICONSET:
            render(source_for(size), size, iconset / f"{name}.png")
        subprocess.run(
            ["iconutil", "--convert", "icns", "--output", str(ICONS / "icon.icns"), str(iconset)],
            check=True,
        )

        frames = []
        for size in ICO_SIZES:
            frame = work / f"ico-{size}.png"
            render(source_for(size), size, frame)
            frames.append((size, frame.read_bytes()))
        write_ico(frames, ICONS / "icon.ico")

        render(LARGE, 192, PUBLIC / "pwa-192x192.png")
        render(LARGE, 512, PUBLIC / "pwa-512x512.png")
        # Maskable: the board stays inside the 80% safe circle.
        render(
            full_bleed(work, "maskable.svg", "-24 -24 228 228"),
            512,
            PUBLIC / "pwa-maskable-512x512.png",
        )
        # iOS rounds the corners itself.
        render(full_bleed(work, "touch.svg", "0 0 180 180"), 180, PUBLIC / "apple-touch-icon.png")

    for target in (PUBLIC / "favicon.svg", PUBLIC / "logo.svg", SITE / "logo.svg"):
        shutil.copyfile(SMALL, target)


if __name__ == "__main__":
    main()
