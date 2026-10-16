# -*- mode: python ; coding: utf-8 -*-
"""One-file sidecar. Torch, Rerun, and the Griffin client stay out."""

from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("coperceive")
datas += collect_data_files("matplotlib")

hidden = [
    "coperceive.app.serve",
    "coperceive.app.server",
    "coperceive.app.jobs",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
]

a = Analysis(
    ["entry.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch",
        "rerun",
        "rerun_sdk",
        "viser",
        "huggingface_hub",
        "tkinter",
        "coperceive.griffin",
        "coperceive.dataset",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="coperceive-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
