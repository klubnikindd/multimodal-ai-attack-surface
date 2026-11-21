"""Каскад L0→L1→L2 (FR-3.10): L1 zero-shot по изображению (SigLIP2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kadr.ai.cascade import SCENE_LABELS, classify_scene, scene_scores
from kadr.ai.embeddings import OnnxEmbeddingBackend

_MODEL_DIR = Path(__file__).resolve().parents[2] / ".models-test" / "siglip2"


def _backend() -> OnnxEmbeddingBackend:
    b = OnnxEmbeddingBackend(_MODEL_DIR)
    if not b.available:
        pytest.skip(b.reason or "SigLIP2 unavailable")
    return b


def test_scene_scores_covers_all_scenes() -> None:
    b = _backend()
    vec = b.embed_text("a dog running on the beach")
    scores = scene_scores(vec, b)
    assert len(scores) == len(SCENE_LABELS)
    confs = [c for _, c in scores]
    assert confs == sorted(confs, reverse=True)
    assert all(-1.0 <= c <= 1.0 for c in confs)


def test_classify_scene_text_food() -> None:
    b = _backend()
    vec = b.embed_text("a plate of pasta with salad")
    scene, conf, needs_vlm = classify_scene(vec, b)
    assert scene in list(SCENE_LABELS) + ["unknown"]
    assert -1.0 <= conf <= 1.0
    assert isinstance(needs_vlm, bool)


def test_classify_scene_image(tmp_path: Path) -> None:
    from PIL import Image

    b = _backend()
    img = tmp_path / "t.jpg"
    Image.new("RGB", (224, 224), (30, 120, 60)).save(img)  # зелёный
    v = b.embed_image(path=img, media_class="photo", scene_type=None)
    scene, conf, needs_vlm = classify_scene(v, b)
    assert scene in list(SCENE_LABELS) + ["unknown"]
    assert -1.0 <= conf <= 1.0
    assert isinstance(needs_vlm, bool)
