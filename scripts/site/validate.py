"""Check the static pages without dependencies or network access."""

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2] / "site"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        for key in ("src", "href"):
            if values.get(key):
                self.links.append(str(values[key]))


def parse(path: Path) -> Page:
    page = Page()
    page.feed(path.read_text(encoding="utf-8"))
    return page


# Published pages: the landing page and the documentation site.
sources = [ROOT / "index.html", *sorted((ROOT / "docs").rglob("*.html"))]
pages = {path.resolve(): parse(path) for path in sources}
count = 0
for path, page in pages.items():
    name = path.relative_to(ROOT).as_posix()
    for link in page.links:
        count += 1
        url = urlsplit(link)
        if url.scheme or url.netloc:
            assert url.scheme == "https", f"{name}: non-HTTPS resource: {link}"
            continue
        assert not url.path.startswith("/"), f"{name}: not sub-path safe: {link}"
        target = (path.parent / unquote(url.path)).resolve() if url.path else path
        assert target.is_relative_to(ROOT), f"{name}: resource escapes site: {link}"
        assert target.exists(), f"{name}: missing resource: {link}"
        if url.fragment:
            linked = pages.get(target / "index.html" if target.is_dir() else target)
            assert linked is not None, f"{name}: anchor on a non-page: {link}"
            assert url.fragment in linked.ids, f"{name}: missing anchor: {link}"
print(f"PASS: {count} links in {len(pages)} pages; relative assets support /Scan64/.")
