"""Check the static entry point without dependencies or network access."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get('id'):
            self.ids.add(str(values['id']))
        for key in ('src', 'href'):
            if values.get(key):
                self.links.append(str(values[key]))


page = Page()
page.feed((ROOT / 'index.html').read_text())
for link in page.links:
    url = urlsplit(link)
    if url.scheme or url.netloc:
        assert url.scheme == 'https', f'Non-HTTPS resource: {link}'
        continue
    assert not url.path.startswith('/'), f'Not sub-path safe: {link}'
    target = (ROOT / unquote(url.path)).resolve()
    assert target.is_relative_to(ROOT), f'Resource escapes site: {link}'
    assert target.exists(), f'Missing resource: {link}'
    if not url.path and url.fragment:
        assert url.fragment in page.ids, f'Missing anchor: {link}'
print(f'PASS: {len(page.links)} HTML resources and links; relative assets support /Scan64/.')
