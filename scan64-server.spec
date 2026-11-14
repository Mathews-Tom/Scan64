# Build through scripts/build-server.py; this is a server, not a GUI application.
from pathlib import Path
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
stage = root / "build" / "server-inputs"
datas = collect_data_files("scan64", include_py_files=True)
datas += [(str(Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"), "notices/python")]
datas += [(str(stage / "python-licenses"), "notices/python/components")]
datas += copy_metadata("scan64", recursive=True)
datas += copy_metadata("pyinstaller")

analysis = Analysis(
    [str(root / "src/scan64/desktop_server.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("scan64")
    + [
        "uvicorn.logging",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.lifespan.on",
    ],
    excludes=["pytest", "mypy", "ruff", "hypothesis", "setuptools", "tkinter", "_tkinter"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="scan64-server",
    console=True,
    target_arch="arm64",
    codesign_identity=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name="scan64-server")
