"""Каскад классификации L0→L1→L2 (FR-3.10).

L0 — дешёвая эвристика (kind, детерминированные маркеры) — уже в classify.py.
L1 — zero-shot по изображению: косинус image-вектора (SigLIP2) к текст-векторам
     сцен (VLM_SCENE_TYPES). Дёшево, офлайн, без VLM.
L2 — VLM: вызывается, когда уверенность L1 ниже порога (не автоматически;
     планировщик ночного VLM использует флаг needs_vlm).

Пороги — предварительные; калибровка по SEC-03 в отдельном шаге.
"""

from __future__ import annotations

import math

from .embeddings import EmbeddingBackend

# scene_type -> текст для text-энкодера (RU-морфология не важна: SigLIP2 англоязычный)
SCENE_LABELS: dict[str, str] = {
    "wedding_ceremony": "wedding ceremony bride and groom",
    "portrait": "portrait photo of a person face",
    "landscape": "landscape nature mountains sea sunset",
    "street": "city street urban road buildings",
    "document": "document paper with printed text",
    "receipt": "receipt check invoice",
    "whiteboard": "whiteboard with diagrams and notes",
    "screenshot": "computer screen screenshot interface",
    "food": "food meal dish plate",
    "product": "product item object on plain background",
    "architecture": "building architecture facade",
    "interior": "room interior inside furniture",
    "event": "event crowd people gathering",
    "sport": "sport game competition athlete",
    "concert": "concert stage performance music lights",
    "other": "miscellaneous other scene",
}

L1_ASSIGN_THRESHOLD = 0.18  # косинус, при котором L1 назначает сцену
L2_NEEDS_VLM_THRESHOLD = 0.08  # ниже — «нужен VLM»

_text_cache: dict[str, list[float]] = {}


def scene_scores(image_vector: list[float], backend: EmbeddingBackend) -> list[tuple[str, float]]:
    """Косинусные близости image-вектора к текст-векторам сцен (по убыванию)."""
    norm_i = math.sqrt(sum(x * x for x in image_vector)) or 1.0
    scores: list[tuple[str, float]] = []
    for scene, label in SCENE_LABELS.items():
        te = _text_cache.get(scene)
        if te is None:
            te = backend.embed_text(label)
            _text_cache[scene] = te
        norm_t = math.sqrt(sum(x * x for x in te)) or 1.0
        dot = sum(a * b for a, b in zip(image_vector, te))
        scores.append((scene, dot / (norm_i * norm_t)))
    return sorted(scores, key=lambda p: p[1], reverse=True)


def classify_scene(image_vector: list[float], backend: EmbeddingBackend) -> tuple[str, float, bool]:
    """L1: возвращает (scene, confidence, needs_vlm)."""
    scores = scene_scores(image_vector, backend)
    top_scene, top_conf = scores[0]
    needs_vlm = top_conf < L2_NEEDS_VLM_THRESHOLD
    if top_conf < L1_ASSIGN_THRESHOLD:
        return "unknown", top_conf, needs_vlm
    return top_scene, top_conf, needs_vlm


__all__ = [
    "L1_ASSIGN_THRESHOLD",
    "L2_NEEDS_VLM_THRESHOLD",
    "SCENE_LABELS",
    "classify_scene",
    "scene_scores",
]
