"""Точка входа one-dir-сборки sidecar (ADR-0005, спайк C).

Проблема: kadr/__main__.py использует относительный импорт (from .cli import
main) — при запуске как скрипта из PyInstaller-бандла это даёт
"attempted relative import with no known parent package". Поэтому entry —
отдельный файл с абсолютным импортом; kadr пакет PyInstaller кладёт сам.
"""

from __future__ import annotations

import sys

from kadr.cli import main

if __name__ == "__main__":
    sys.exit(main())
