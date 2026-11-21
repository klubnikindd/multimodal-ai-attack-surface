"""FR-3.9: лица (SCRFD-детектор + SigLIP2-энкодер + кластеризация без имён)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kadr.ai.embeddings import OnnxEmbeddingBackend
from kadr.ai.faces import cluster_faces, detect_faces, embed_face_crops

_INPUTS = Path(__file__).resolve().parents[2] / "data" / "golden" / "v1" / "inputs"


def _face_photo() -> Path | None:
    for name in ("DSCF1789.JPG", "DSCF1790.JPG"):
        p = _INPUTS / name
        if p.is_file():
            return p
    for p in sorted(_INPUTS.glob("*.jpg")):
        return p
    return None


def _backend() -> OnnxEmbeddingBackend:
    b = OnnxEmbeddingBackend(Path(__file__).resolve().parents[2] / ".models-test" / "siglip2")
    if not b.available:
        pytest.skip(b.reason or "SigLIP2 unavailable")
    return b


def test_detect_faces() -> None:
    from PIL import Image

    p = _face_photo()
    if p is None:
        pytest.skip("нет golden-фото")
    img = Image.open(p)
    boxes = detect_faces(img)
    assert len(boxes) >= 1
    for x1, y1, x2, y2, score in boxes:
        assert 0 <= x1 < x2 <= img.width
        assert 0 <= y1 < y2 <= img.height
        assert 0.0 <= score <= 1.0


def test_embed_and_cluster_faces() -> None:
    from PIL import Image

    p = _face_photo()
    if p is None:
        pytest.skip("нет golden-фото")
    b = _backend()
    img = Image.open(p)
    boxes = detect_faces(img)[:5]
    vecs = embed_face_crops(img, boxes, b)
    assert len(vecs) == len(boxes)
    assert all(len(v) == 768 for v in vecs)
    clusters = cluster_faces(vecs)
    assert len(clusters) >= 1
    assert sum(len(c) for c in clusters) == len(vecs)
