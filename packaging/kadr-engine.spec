# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: только one-dir (D10, ADR-0005).

Запрещено: `-F`/onefile, симметрия с Tauri target-triple выполняется
packaging/rename-sidecar.py. CI проверяет этот файл на one-file маркеры.
"""

from pathlib import Path

REPO = Path(SPECPATH).resolve().parents[1]

a = Analysis(
    [str(REPO / "kadr" / "__main__.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=[
        (str(REPO / "kadr" / "catalog" / "schema.sql"), "kadr/catalog"),
        (str(REPO / "models" / "manifest.yaml"), "models"),
        (str(REPO / "security" / "sandbox" / "policy.yaml"), "kadr/security"),
        (str(REPO / "KNOWN_LIMITATIONS.md"), "."),
    ],
    hiddenimports=["kadr.api", "uvicorn", "fastapi"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kadr-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="kadr-engine",
)
