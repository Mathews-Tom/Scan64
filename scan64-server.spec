# Build through scripts/build-server.py; Windows must not create a console.
from pathlib import Path
import sys
import sysconfig

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH)
stage = root / "build" / "server-inputs"
datas = collect_data_files("scan64", include_py_files=True)
python_license = next(
    candidate
    for candidate in (
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
        Path(sys.base_prefix) / "LICENSE.txt",
    )
    if candidate.is_file()
)
datas += [(str(python_license), "notices/python")]
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
    console=sys.platform != "win32",
    target_arch="arm64" if sys.platform == "darwin" else None,
    codesign_identity=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas, name="scan64-server")
