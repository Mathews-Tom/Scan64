# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow==13.0.0"]
# ///
"""Turn the frames from apps/scan64-web/scripts/capture-readme.mjs into README GIFs.

    uv run scripts/readme-media/make_gifs.py --frames D:/scan64-readme/frames --out assets

Each scene directory holds numbered PNG frames and a frames.json listing each file and
its seconds on screen. A scene becomes ``<out>/demo-<scene>.gif``, looping, at the
frames' size. A GIF over ``--max-kb`` fails the run: the README must load quickly.
"""

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

logger = logging.getLogger("make_gifs")

SCENES = ("import", "turning-point", "analysis-board")


def make_gif(scene_dir: Path, target: Path, colours: int) -> int:
    frames = json.loads((scene_dir / "frames.json").read_text(encoding="utf-8"))
    if not frames:
        raise ValueError(f"{scene_dir}: no frames")
    images = [Image.open(scene_dir / frame["file"]).convert("RGB") for frame in frames]
    sizes = {image.size for image in images}
    if len(sizes) != 1:
        raise ValueError(f"{scene_dir}: frames differ in size {sizes}")
    # One palette for the scene keeps colours steady from frame to frame.
    palette = images[0].quantize(colors=colours, method=Image.Quantize.MEDIANCUT)
    quantized = [image.quantize(palette=palette, dither=Image.Dither.NONE) for image in images]
    quantized[0].save(
        target,
        save_all=True,
        append_images=quantized[1:],
        duration=[round(frame["seconds"] * 1000) for frame in frames],
        loop=0,
        optimize=True,
        disposal=1,
    )
    return target.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--frames", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--colours", type=int, default=64)
    parser.add_argument("--max-kb", type=int, default=400)
    args = parser.parse_args()
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(args.frames / f"make_gifs_{stamp}.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    for scene in SCENES:
        target = args.out / f"demo-{scene}.gif"
        size = make_gif(args.frames / scene, target, args.colours)
        logger.info("%s: %d KB", target, size // 1024)
        if size > args.max_kb * 1024:
            raise ValueError(f"{target} is {size // 1024} KB, over --max-kb {args.max_kb}")


if __name__ == "__main__":
    main()
