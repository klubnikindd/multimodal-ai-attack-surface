"""Resource-bomb корпус (SEC-09/T09): decompression-bomb, pixel-bomb, zip-bomb."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from kadr.workers.client import WorkerError, run_worker


def _huge_png(path: Path, w: int = 100000, h: int = 100000) -> None:
    """PNG с огромными размерами в заголовке (decompression-bomb, маленький на диске)."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    ihdr_chunk = (
        struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    )
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    path.write_bytes(sig + ihdr_chunk + iend)


def test_decompression_bomb_rejected(tmp_path: Path) -> None:
    """PNG 100000×100000 (10^10 px) обязан быть отклонён, а не декодирован."""
    p = tmp_path / "bomb.png"
    _huge_png(p)
    assert p.stat().st_size < 1000  # маленький на диске
    with pytest.raises(WorkerError):
        run_worker("metadata", p)


def test_pixel_bomb_rejected(tmp_path: Path) -> None:
    from PIL import Image

    p = tmp_path / "big.jpg"
    Image.new("RGB", (600, 600), color=(0, 0, 0)).save(p)  # 360K px
    with pytest.raises(WorkerError) as exc_info:
        run_worker("metadata", p, limits={"max_pixels": 1000})
    assert "pixel" in str(exc_info.value).lower() or "bomb" in str(exc_info.value).lower()


def test_zip_bomb_skipped_by_scan(tmp_path: Path) -> None:
    """ZIP-бомба (высокий compression ratio) не извлекается сканером (неподдерживаемый тип)."""
    import zipfile

    from kadr.catalog.repository import Catalog
    from kadr.config import Settings
    from kadr.scanner import scan_root

    media = tmp_path / "media"
    media.mkdir()
    p = media / "bomb.zip"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("payload.txt", "0" * 50_000_000)  # 50MB несжатых нулей
    assert p.stat().st_size < 100_000  # сильно сжато

    settings = Settings(home=tmp_path / ".kadr", library_root=tmp_path / "lib")
    catalog = Catalog(settings.catalog_path)
    report = scan_root(catalog, media, settings)
    # zip индексируется как opaque "other", НЕ извлекается (payload.txt не появляется)
    assert not (media / "payload.txt").exists()
    rows = catalog.list_assets()
    assert rows and rows[0]["kind"] == "other"
