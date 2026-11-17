from __future__ import annotations

from pathlib import Path

import pytest

from kadr.catalog.repository import Catalog
from kadr.config import Settings

try:
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None


@pytest.fixture()
def settings(tmp_path):
    value = Settings(home=tmp_path / ".kadr", library_root=tmp_path / "library")
    value.ensure_dirs()
    return value


@pytest.fixture()
def catalog(settings):
    db = Catalog(settings.catalog_path)
    try:
        yield db
    finally:
        db.close()


def make_image(
    path: Path,
    *,
    color=(160, 100, 60),
    size=(120, 80),
    taken: str | None = "2024:07:15 13:22:00",
    make: str | None = "Canon",
    model: str | None = "EOS R",
    fmt: str | None = None,
) -> Path:
    if Image is None:  # pragma: no cover
        pytest.skip("Pillow is not installed")
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color=color)
    exif = Image.Exif()
    if taken:
        exif[36867] = taken
        exif[36868] = taken
    if make:
        exif[271] = make
    if model:
        exif[272] = model
    exif[274] = 1
    image.save(path, format=fmt, exif=exif)
    return path


@pytest.fixture()
def source_dir(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    make_image(root / "IMG_20240715_132200.jpg", color=(200, 100, 60), taken="2024:07:15 13:22:00")
    make_image(root / "IMG_20240716_142200.jpg", color=(60, 100, 200), taken="2024:07:16 14:22:00")
    make_image(
        root / "Screenshot 2025-01-02.png", color=(10, 180, 90), taken=None, make=None, model=None
    )
    (root / "IMG_20240715_132200.xmp").write_text(
        """<?xml version="1.0"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/">
 <rdf:RDF><rdf:Description rdf:about="">
  <dc:subject><rdf:Bag><rdf:li>existing</rdf:li></rdf:Bag></dc:subject>
 </rdf:Description></rdf:RDF>
</x:xmpmeta>""",
        encoding="utf-8",
    )
    return root
