#!/usr/bin/env python3
"""Regenerate every Scan64 icon and preview image from the committed SVG sources.

``icons/source/icon-large.svg`` (the knight's tour on a board) is used above 32 px;
``icons/source/icon-small.svg`` (the tour alone) is used at 32 px and below.
``assets/banner.svg`` (the README banner) is padded into the GitHub social preview
and the website's link preview. Requires macOS ``iconutil`` and ``sips`` and
``rsvg-convert`` (``brew install librsvg``). Run ``python3 scripts/build-brand-assets.py``
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
ASSETS = ROOT / "assets"
BANNER = ASSETS / "banner.svg"
BANNER_VIEWBOX = 'viewBox="0 0 1280 420" width="1280" height="420"'
BANNER_GROUND = '<rect width="1280" height="420" fill="#232323"/>'
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


def render(svg: Path, width: int, output: Path, height: int | None = None) -> None:
    subprocess.run(
        [
            "rsvg-convert",
            "--width",
            str(width),
            "--height",
            str(height or width),
            "--output",
            str(output),
            str(svg),
        ],
        check=True,
    )


def card(work: Path, name: str, width: int, height: int) -> Path:
    """The banner centred on an ink canvas with the given aspect ratio."""
    text = BANNER.read_text(encoding="utf-8")
    if BANNER_VIEWBOX not in text or BANNER_GROUND not in text:
        raise SystemExit(
            "banner.svg no longer has the expected frame; update build-brand-assets.py"
        )
    total = 1280 * height / width
    pad = (total - 420) / 2
    frame = f'viewBox="0 {-pad:g} 1280 {total:g}" width="{width}" height="{height}"'
    text = text.replace(BANNER_VIEWBOX, frame).replace(
        BANNER_GROUND, f'<rect y="{-pad:g}" width="1280" height="{total:g}" fill="#232323"/>'
    )
    path = work / name
    path.write_text(text, encoding="utf-8")
    return path


def full_bleed(work: Path, name: str, view_box: str) -> Path:
    """Large artwork on an unrounded ink square, for platforms that apply their own mask."""
    text = LARGE.read_text(encoding="utf-8")
    if INSET_VIEWBOX not in text or TILE not in text:
        raise SystemExit(
            "icon-large.svg no longer has the expected tile; update build-brand-assets.py"
        )
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
    for tool in ("rsvg-convert", "iconutil", "sips"):
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

        render(card(work, "social.svg", 1280, 640), 1280, ASSETS / "social-preview.png", 640)
        share = work / "share.png"
        render(card(work, "share.svg", 1200, 630), 1200, share, 630)
        subprocess.run(
            [
                "sips",
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "90",
                str(share),
                "--out",
                str(SITE / "share.jpg"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )

    for target in (PUBLIC / "favicon.svg", PUBLIC / "logo.svg", SITE / "logo.svg"):
        shutil.copyfile(SMALL, target)
    shutil.copyfile(LARGE, ASSETS / "logo.svg")


if __name__ == "__main__":
    main()
