from __future__ import annotations

from pathlib import Path

import pytest

from kadr.errors import PathSafetyError
from kadr.xmp import (
    export_metadata,
    read_xmp_sidecar,
    sidecar_path,
    write_embedded_metadata,
    write_xmp_sidecar,
)


def test_sidecar_merge_never_overwrites_user_fields(tmp_path):
    image = tmp_path / "photo.jpg"
    image.write_bytes(b"not a real jpeg")
    existing = sidecar_path(image)
    existing.write_text(
        """<?xml version="1.0"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
 <rdf:RDF><rdf:Description rdf:about="">
  <dc:subject><rdf:Bag><rdf:li>user-tag</rdf:li></rdf:Bag></dc:subject>
  <dc:description><rdf:Alt><rdf:li xml:lang="x-default">user description</rdf:li></rdf:Alt></dc:description>
  <xmp:Rating>5</xmp:Rating>
 </rdf:Description></rdf:RDF>
</x:xmpmeta>""",
        encoding="utf-8",
    )
    result = write_xmp_sidecar(
        image,
        tags=["model-tag", "user-tag"],
        description="model description",
        rating=1,
        provenance={"model": "test", "confidence": 0.9},
        preset="archive",
    )
    assert result.written
    data = read_xmp_sidecar(image)
    assert "user-tag" in data["tags"]
    assert "model-tag" in data["tags"]
    assert data["description"] == "user description"
    assert data["rating"] == 5


def test_raw_embedded_write_is_forbidden(tmp_path):
    raw = tmp_path / "photo.CR2"
    raw.write_bytes(b"raw")
    with pytest.raises(PathSafetyError):
        write_embedded_metadata(raw, tags=["x"])


def test_privacy_presets_strip_gps_and_pii():
    metadata = {
        "gps": {"lat": 1.0, "lon": 2.0},
        "camera_serial": "1234",
        "faces": [1, 2],
        "description": "Иван, ivan@example.com",
        "tags": ["ok", "ivan@example.com"],
    }
    stock = export_metadata(metadata, preset="stock")
    assert "gps" not in stock
    assert "camera_serial" not in stock
    assert "faces" not in stock
    assert "ivan@example.com" not in stock["tags"]
    archive = export_metadata(metadata, preset="archive")
    assert archive["gps"]
    social = export_metadata(metadata, preset="social")
    assert "description" not in social


def test_embedded_write_preserves_exif_icc_and_returns_backup(tmp_path):
    from PIL import Image, ImageCms

    image = tmp_path / "photo.jpg"
    exif = Image.Exif()
    exif[36867] = "2024:07:15 13:22:00"
    exif[271] = "Canon"
    exif[272] = "EOS R"
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    Image.new("RGB", (32, 24), (120, 80, 40)).save(image, exif=exif, icc_profile=icc, quality=90)

    result = write_embedded_metadata(image, tags=["test"], description="hello", preset="archive")
    assert result.written is True
    assert result.backup and Path(result.backup).is_file()
    with Image.open(image) as saved:
        saved_exif = saved.getexif()
        assert len(saved_exif) >= 3
        assert saved_exif.get(36867) == "2024:07:15 13:22:00"
        assert saved.info.get("icc_profile")
        assert b"test" in saved.info.get("xmp", b"")
