"""FR-3.11: ансамбль (CLIP↔VLM согласие) + TTA (test-time augmentation).

SEC-03 (калибровка): когда TTA нестабилен или CLIP↔VLM расходятся —
не принимаем решение, флагуем на ревью.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .cascade import classify_scene
from .embeddings import EmbeddingBackend


def tta_scene(image: Any, backend: EmbeddingBackend) -> dict[str, Any]:
    """TTA: прогон L1 на оригинале + горизонтальном флипе.

    Возвращает {scene, confidence, flip_scene, flip_confidence, agreement}.
    """
    from PIL import Image
    import tempfile

    img = image.convert("RGB") if isinstance(image, Image.Image) else Image.open(image).convert("RGB")
    flipped = img.transpose(Image.FLIP_LEFT_RIGHT)

    vecs = []
    with tempfile.TemporaryDirectory() as td:
        for im in (img, flipped):
            p = Path(td) / "t.jpg"
            im.save(p, "JPEG")
            vecs.append(backend.embed_image(path=p, media_class="photo", scene_type=None))

    scene, conf, _ = classify_scene(vecs[0], backend)
    flip_scene, flip_conf, _ = classify_scene(vecs[1], backend)
    return {
        "scene": scene,
        "confidence": round(conf, 4),
        "flip_scene": flip_scene,
        "flip_confidence": round(flip_conf, 4),
        "agreement": scene == flip_scene,
    }


def ensemble_agree(siglip_scene: str | None, vlm_scene: str | None) -> bool | None:
    """Согласие CLIP↔VLM. None = неопределённо (недостаточно данных)."""
    if siglip_scene in (None, "unknown") or vlm_scene in (None, "unknown"):
        return None
    return siglip_scene == vlm_scene


__all__ = ["ensemble_agree", "tta_scene"]
