"""Build the documentation site (site/docs/) from the Markdown files in docs.toml.

The pages are generated and committed: the Pages workflow publishes site/ as
it is. tests/unit/test_docs_site.py fails when the committed pages differ
from what this script builds.

    uv run python scripts/site/build_docs.py            # write site/docs/
    uv run python scripts/site/build_docs.py --out DIR  # write elsewhere
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import markdown

REPO = Path(__file__).resolve().parents[2]
MANIFEST = REPO / "scripts" / "site" / "docs.toml"
STYLESHEET = REPO / "scripts" / "site" / "docs.css"
MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
GITHUB = "https://github.com/Mathews-Tom/Scan64"
LOGO = '<img src="../logo.svg" width="28" height="28" alt="">'


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    source: PurePosixPath
    summary: str


@dataclass(frozen=True)
class Manifest:
    repository: str
    landing: str
    sections: tuple[tuple[str, tuple[Page, ...]], ...]

    @property
    def pages(self) -> tuple[Page, ...]:
        return tuple(page for _, pages in self.sections for page in pages)


def load_manifest(path: Path = MANIFEST) -> Manifest:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    sections = tuple(
        (
            section["title"],
            tuple(
                Page(p["slug"], p["title"], PurePosixPath(p["source"]), p["summary"])
                for p in section["pages"]
            ),
        )
        for section in data["section"]
    )
    manifest = Manifest(data["repository"], data["landing"], sections)
    slugs = [page.slug for page in manifest.pages]
    if len(set(slugs)) != len(slugs) or "index" in slugs:
        raise ValueError(f"Page slugs must be unique and not 'index': {slugs}")
    for page in manifest.pages:
        if not (REPO / page.source).is_file():
            raise FileNotFoundError(f"Missing page source: {page.source}")
    return manifest


def github_slug(value: str, separator: str = "-") -> str:
    """GitHub's heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    value = value.strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", separator)


def _normalise(path: PurePosixPath) -> PurePosixPath:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if not parts:
                raise ValueError(f"Link leaves the repository: {path}")
            parts.pop()
        elif part != ".":
            parts.append(part)
    return PurePosixPath(*parts)


class Linker:
    """Rewrites one page's links for the site and collects the media it uses."""

    def __init__(self, manifest: Manifest, page: Page, media: dict[PurePosixPath, str]):
        self.manifest = manifest
        self.page = page
        self.media = media
        self.by_source = {p.source: p for p in manifest.pages}

    def target(self, link: str) -> str:
        url = urlsplit(link)
        if url.scheme or url.netloc or not url.path:
            return link
        fragment = f"#{url.fragment}" if url.fragment else ""
        path = _normalise(self.page.source.parent / url.path)
        if path in self.by_source:
            return f"{self.by_source[path].slug}.html{fragment}"
        if path.suffix.lower() in MEDIA_SUFFIXES and (REPO / path).is_file():
            name = f"media/{path.name}"
            if any(other != path and used == name for other, used in self.media.items()):
                raise ValueError(f"Two media files are named {path.name}")
            self.media[path] = name
            return name
        if not (REPO / path).exists():
            raise FileNotFoundError(f"{self.page.source} links to missing {path}")
        return f"{self.manifest.repository}{path}{fragment}"

    def rewrite(self, body: str) -> str:
        def replace(match: re.Match[str]) -> str:
            value = html.unescape(match.group(2))
            return f'{match.group(1)}="{html.escape(self.target(value), quote=True)}"'

        return re.sub(r'\b(href|src)="([^"]*)"', replace, body)


def render_markdown(text: str) -> str:
    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists", "md_in_html"],
        extension_configs={"toc": {"slugify": github_slug, "permalink": False}},
        output_format="html",
    )
    return converter.convert(text)


def _navigation(manifest: Manifest, current: str) -> str:
    groups = []
    for title, pages in manifest.sections:
        items = "".join(
            f'<li><a href="{p.slug}.html"'
            + (' aria-current="page"' if p.slug == current else "")
            + f">{html.escape(p.title)}</a></li>"
            for p in pages
        )
        groups.append(f"<h2>{html.escape(title)}</h2><ul>{items}</ul>")
    return "".join(groups)


def _document(manifest: Manifest, current: str, title: str, description: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Scan64 documentation</title>
<meta name="description" content="{html.escape(description, quote=True)}">
<link rel="icon" href="../logo.svg" type="image/svg+xml">
<link rel="stylesheet" href="docs.css">
</head>
<body>
<a class="skip-link" href="#content">Skip to content</a>
<header class="docs-bar"><a class="docs-home" href="index.html">{LOGO}Scan64 documentation</a>
<nav aria-label="Site"><a class="docs-pages" href="#pages">Pages</a>
<a href="{manifest.landing}">Scan64 home</a>
<a href="{GITHUB}">Source on GitHub</a></nav>
</header>
<div class="docs-layout">
<nav id="pages" class="docs-nav" aria-label="Documentation">{_navigation(manifest, current)}</nav>
<main id="content" class="docs-content">
{body}
</main>
</div>
<footer class="docs-footer"><p>Generated from the Markdown files in the
<a href="{GITHUB}/tree/main/docs">docs folder</a>. Scan64 is licensed under the
<a href="{GITHUB}/blob/main/LICENSE">GNU GPL v3.0</a>.</p></footer>
</body>
</html>
"""


def build(out: Path, manifest: Manifest | None = None) -> list[Path]:
    """Write every page, the index, the stylesheet and used media into ``out``."""
    manifest = manifest or load_manifest()
    media: dict[PurePosixPath, str] = {}
    written: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    for page in manifest.pages:
        text = (REPO / page.source).read_text(encoding="utf-8")
        body = Linker(manifest, page, media).rewrite(render_markdown(text))
        target = out / f"{page.slug}.html"
        target.write_text(
            _document(manifest, page.slug, page.title, page.summary, body),
            encoding="utf-8",
            newline="\n",
        )
        written.append(target)
    cards = "".join(
        f"<h2>{html.escape(title)}</h2><dl>"
        + "".join(
            f'<dt><a href="{p.slug}.html">{html.escape(p.title)}</a></dt>'
            f"<dd>{html.escape(p.summary)}</dd>"
            for p in pages
        )
        + "</dl>"
        for title, pages in manifest.sections
    )
    index = (
        "<h1>Scan64 documentation</h1>"
        "<p>Scan64 is a free, open-source chess coach that runs on your own computer. "
        "These pages explain how to install it, use every screen, configure the server "
        "and how the system works.</p>" + cards
    )
    target = out / "index.html"
    target.write_text(
        _document(manifest, "index", "Overview", "Scan64 documentation.", index),
        encoding="utf-8",
        newline="\n",
    )
    written.append(target)
    shutil.copyfile(STYLESHEET, out / "docs.css")
    written.append(out / "docs.css")
    for source, name in sorted(media.items()):
        destination = out / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / source, destination)
        written.append(destination)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "site" / "docs")
    args = parser.parse_args()
    files = build(args.out)
    print(f"Wrote {len(files)} files to {args.out}")


if __name__ == "__main__":
    main()
