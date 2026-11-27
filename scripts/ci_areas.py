"""Select the CI areas that a pull request's changed files can affect.

Reads changed paths, one per line, on stdin and prints one ``<area>=true|false``
line per area, in the format of ``$GITHUB_OUTPUT``. ``--all`` selects every
area without reading stdin; CI uses it for pushes and pull requests into
``main``.

The first rule whose pattern matches a path decides that path's areas. A path
that no rule matches selects every area, so a new kind of file can never skip a
check: add a rule for it instead. ``*`` in a pattern also matches ``/``.
"""

from __future__ import annotations

import sys
from fnmatch import fnmatchcase

AREAS = ("python", "web", "desktop")

ALL = frozenset(AREAS)
NONE: frozenset[str] = frozenset()
PYTHON = frozenset({"python"})
WEB = frozenset({"web"})
DESKTOP = frozenset({"desktop"})
PYTHON_DESKTOP = frozenset({"python", "desktop"})
PYTHON_WEB = frozenset({"python", "web"})
WEB_DESKTOP = frozenset({"web", "desktop"})

RULES: tuple[tuple[str, frozenset[str]], ...] = (
    # The gate's own definition: a change to it runs everything.
    (".github/workflows/ci.yml", ALL),
    (".github/actions/*", ALL),
    ("scripts/check.sh", ALL),
    ("scripts/ci_areas.py", ALL),
    # Workflows that run on their own triggers, and repository metadata.
    (".github/workflows/desktop.yml", DESKTOP),
    (".github/*", NONE),
    # Desktop shell, frozen server and the files bundled into installers.
    ("apps/scan64-desktop/*", DESKTOP),
    ("src/scan64/desktop_*", PYTHON_DESKTOP),
    ("src/scan64/api/desktop_security.py", PYTHON_DESKTOP),
    ("scripts/build-server.py", PYTHON_DESKTOP),
    ("scripts/desktop-*", PYTHON_DESKTOP),
    ("scan64-server.spec", DESKTOP),
    ("Dockerfile", DESKTOP),
    ("LICENSE", PYTHON_DESKTOP),
    ("THIRD_PARTY_NOTICES.md", PYTHON_DESKTOP),
    # Dependency locks shared with the desktop build.
    ("pyproject.toml", PYTHON_DESKTOP),
    ("uv.lock", PYTHON_DESKTOP),
    (".python-version", PYTHON_DESKTOP),
    ("pnpm-lock.yaml", WEB_DESKTOP),
    ("pnpm-workspace.yaml", WEB_DESKTOP),
    # Web client; its API types follow the JSON schemas.
    ("apps/scan64-web/*", WEB),
    ("schemas/*", PYTHON_WEB),
    # Backend, its tests, benchmarks and tools.
    ("src/*", PYTHON),
    ("tests/*", PYTHON),
    ("benchmarks/*", PYTHON),
    ("config/*", PYTHON),
    ("scripts/*", PYTHON),
    # Tests read these reports and examples.
    ("docs/evaluation/*", PYTHON),
    ("docs/*.json", PYTHON),
    # Prose and images that no check reads. The Pages workflow checks site/.
    ("docs/*.md", NONE),
    ("assets/*", NONE),
    ("site/*", NONE),
    ("*.md", NONE),
)


def areas_for(path: str) -> frozenset[str]:
    """Return the areas one changed path can affect."""
    for pattern, areas in RULES:
        if fnmatchcase(path, pattern):
            return areas
    return ALL


def select(paths: list[str]) -> frozenset[str]:
    """Return the union of the areas every changed path can affect."""
    selected: set[str] = set()
    for path in paths:
        selected |= areas_for(path)
    return frozenset(selected)


def main(argv: list[str]) -> int:
    if argv == ["--all"]:
        selected = ALL
    elif argv:
        print("usage: ci_areas.py [--all] < changed-paths", file=sys.stderr)
        return 2
    else:
        paths = [line.strip() for line in sys.stdin if line.strip()]
        if not paths:
            print("ci_areas.py: no changed paths on stdin", file=sys.stderr)
            return 1
        selected = select(paths)
    for area in AREAS:
        print(f"{area}={'true' if area in selected else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
