# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: только one-dir (D10, ADR-0005, спайк C).

Запрещено: -F/onefile (CI проверяет check-no-onefile.py); симметрия с Tauri
target-triple выполняется packaging/rename-sidecar.py.

История правок (спайк C, Ф1):
  * REPO = Path(SPECPATH).resolve().parents[1] → ошибка "script kadr/__main__.py
    not found": SPECPATH = каталог spec-файла (packaging/), корень — его parent.
  * entry — packaging/entry.py: kadr/__main__.py использует относительный
    импорт и из PyInstaller-бандла падает с ImportError.
"""

from pathlib import Path

REPO = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(REPO / "packaging" / "entry.py")],
    pathex=[str(REPO)],
    binaries=[],
    datas=[
        (str(REPO / "kadr" / "catalog" / "schema.sql"), "kadr/catalog"),
        (str(REPO / "models" / "manifest.yaml"), "models"),
        (str(REPO / "security" / "sandbox" / "policy.yaml"), "kadr/security"),
        (str(REPO / "KNOWN_LIMITATIONS.md"), "."),
    ],
    # sqlite_vec обязателен: без него бандл честно деградирует до exact (спайки A/C),
    # но продукт должен уметь везти расширение; PyInstaller подхватит его .pyd.
    hiddenimports=["kadr.api", "uvicorn", "fastapi", "sqlite_vec"],
    hookspath=[str(REPO / "packaging")],  # hook-sqlite_vec.py
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
