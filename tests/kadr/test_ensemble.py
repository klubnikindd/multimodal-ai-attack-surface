"""FR-3.11: ансамбль + TTA."""

from __future__ import annotations

from pathlib import Path

import pytest

from kadr.ai.embeddings import OnnxEmbeddingBackend
from kadr.ai.ensemble import ensemble_agree, tta_scene

_INPUTS = Path(__file__).resolve().parents[2] / "data" / "golden" / "v1" / "inputs"


def test_ensemble_agree_pure() -> None:
    assert ensemble_agree("landscape", "landscape") is True
    assert ensemble_agree("landscape", "portrait") is False
    assert ensemble_agree(None, "landscape") is None
    assert ensemble_agree("unknown", "landscape") is None


def test_tta_scene() -> None:
    from PIL import Image

    p = _INPUTS / "DSCF1789.JPG"
    if not p.is_file():
        pytest.skip("нет golden-фото")
    b = OnnxEmbeddingBackend(Path(__file__).resolve().parents[2] / ".models-test" / "siglip2")
    if not b.available:
        pytest.skip(b.reason or "SigLIP2 unavailable")
    img = Image.open(p)
    r = tta_scene(img, b)
    assert set(r) == {"scene", "confidence", "flip_scene", "flip_confidence", "agreement"}
    assert isinstance(r["agreement"], bool)
    assert -1.0 <= r["confidence"] <= 1.0
