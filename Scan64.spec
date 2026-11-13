# Build only through scripts/build-macos-app.sh (macOS arm64).
from importlib.metadata import distributions
from pathlib import Path
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
stage = root / "build" / "macos-inputs"
datas = collect_data_files("scan64", include_py_files=True)
datas += [(str(root / "apps/scan64-web/dist"), "web")]
datas += [(str(stage / "stockfish"), "notices/stockfish")]
datas += [(str(root / "LICENSE"), "notices/scan64")]
datas += [(str(root / "THIRD_PARTY_NOTICES.md"), "notices/scan64")]
datas += [(str(Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"), "notices/python")]
datas += [(str(stage / "tcl-tk"), "notices/tcl-tk")]
# Preserve dependency metadata and upstream license files, not just importable code.
for distribution in distributions():
    datas += copy_metadata(distribution.metadata["Name"])

analysis = Analysis(
    [str(root / "src/scan64/desktop.py")],
    pathex=[str(root / "src")],
    binaries=[(str(stage / "bin/stockfish"), "bin")],
    datas=datas,
    hiddenimports=collect_submodules("scan64")
    + [
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
    ],
    excludes=["pytest", "mypy", "ruff", "hypothesis", "setuptools"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Scan64",
    console=False,
    target_arch="arm64",
    codesign_identity=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name="Scan64")
app = BUNDLE(
    collection,
    name="Scan64.app",
    bundle_identifier="org.scan64.desktop",
    info_plist={
        "CFBundleShortVersionString": __import__("importlib.metadata").metadata.version("scan64"),
        "NSHighResolutionCapable": True,
    },
)
