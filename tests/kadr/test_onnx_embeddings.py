"""SigLIP2 ONNX эмбеддинги (вшивание, int8). Пропускается без весов."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from kadr.ai.embeddings import SIGLIP2_DIM, SIGLIP2_MODEL_ID, OnnxEmbeddingBackend

_MODEL_DIR = Path(__file__).resolve().parents[2] / ".models-test" / "siglip2"


def _backend() -> OnnxEmbeddingBackend:
    b = OnnxEmbeddingBackend(_MODEL_DIR)
    if not b.available:
        pytest.skip(b.reason or "SigLIP2 ONNX unavailable")
    return b


def _l2(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def test_onnx_text_embedding() -> None:
    b = _backend()
    assert b.model_id == SIGLIP2_MODEL_ID
    assert b.dim == SIGLIP2_DIM == 768
    v = b.embed_text("ночная улица с неоновыми вывесками")
    assert len(v) == 768
    assert abs(_l2(v) - 1.0) < 1e-3


def test_onnx_image_embedding(tmp_path: Path) -> None:
    from PIL import Image

    b = _backend()
    img = tmp_path / "t.jpg"
    Image.new("RGB", (120, 90), (200, 30, 30)).save(img)
    v = b.embed_image(path=img, media_class="photo", scene_type=None)
    assert len(v) == 768
    assert abs(_l2(v) - 1.0) < 1e-3


def test_onnx_deterministic() -> None:
    b = _backend()
    a = b.embed_text("красный закат над городом")
    c = b.embed_text("красный закат над городом")
    sim = sum(x * y for x, y in zip(a, c))
    assert sim > 0.999
