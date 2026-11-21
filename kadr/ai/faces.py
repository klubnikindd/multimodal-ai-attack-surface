"""Лица (FR-3.9): SCRFD-детектор + SigLIP2-энкодер + кластеризация без имён.

Детектор — SCRFD-500M ONNX (apache-2.0, .models-test/faces/scrfd_500m.onnx).
Энкодер лица — SigLIP2 (уже запинен, apache-2.0): эмбеддинг кропа лица.
Кластеризация — жадная по косинусу; имена присваивает пользователь (локально).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

_STRIDES = (8, 16, 32)
_ANCHOR_SIZES = {8: (16.0, 32.0), 16: (64.0, 128.0), 32: (256.0, 512.0)}
_VARIANCE = (0.1, 0.2)
_INPUT_SIZE = 640
_SCORE_THRESHOLD = 0.5
_NMS_IOU = 0.4

_session: Any = None
_failed = False
_anchors_cache: dict[int, np.ndarray] = {}


def model_dir() -> Path | None:
    import os

    env = os.environ.get("KADR_FACES_DIR")
    candidates: list[Path] = []
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[2] / ".models-test" / "faces")
    for c in candidates:
        if (c / "scrfd_500m.onnx").is_file():
            return c
    return None


def detector() -> Any:
    global _session, _failed
    if _session is not None:
        return _session
    if _failed:
        return None
    d = model_dir()
    if d is None:
        _failed = True
        return None
    try:
        import onnxruntime as ort

        _session = ort.InferenceSession(str(d / "scrfd_500m.onnx"), providers=["CPUExecutionProvider"])
        return _session
    except Exception:
        _failed = True
        return None


def _anchors_for(stride: int) -> np.ndarray:
    if stride in _anchors_cache:
        return _anchors_cache[stride]
    feat = _INPUT_SIZE // stride
    rows = []
    for y in range(feat):
        for x in range(feat):
            cx = (x + 0.5) * stride
            cy = (y + 0.5) * stride
            for s in _ANCHOR_SIZES[stride]:
                rows.append((cx, cy, s, s))
    arr = np.asarray(rows, dtype=np.float32)
    _anchors_cache[stride] = arr
    return arr


def _decode(outputs: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    all_boxes: list[np.ndarray] = []
    all_scores: list[np.ndarray] = []
    for i, stride in enumerate(_STRIDES):
        score = np.asarray(outputs[i], dtype=np.float32).reshape(-1)
        bbox = np.asarray(outputs[i + 3], dtype=np.float32).reshape(-1, 4)
        a = _anchors_for(stride)
        dx, dy, dw, dh = bbox[:, 0], bbox[:, 1], bbox[:, 2], bbox[:, 3]
        cx = a[:, 0] + dx * _VARIANCE[0] * a[:, 2]
        cy = a[:, 1] + dy * _VARIANCE[0] * a[:, 3]
        w = a[:, 2] * np.exp(dw * _VARIANCE[1])
        h = a[:, 3] * np.exp(dh * _VARIANCE[1])
        x1 = cx - w / 2.0
        y1 = cy - h / 2.0
        x2 = cx + w / 2.0
        y2 = cy + h / 2.0
        all_boxes.append(np.stack([x1, y1, x2, y2], axis=1))
        all_scores.append(score)
    return np.concatenate(all_boxes, axis=0), np.concatenate(all_scores, axis=0)


def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / (area_i + area_o - inter + 1e-6)
        order = rest[np.where(iou <= iou_threshold)[0]]
    return keep


def detect_faces(
    image: Any, *, threshold: float = _SCORE_THRESHOLD
) -> list[tuple[float, float, float, float, float]]:
    """Детектирует лица. Возвращает [(x1,y1,x2,y2,score)] в координатах исходного изображения."""
    from PIL import Image

    session = detector()
    if session is None:
        return []
    img = image.convert("RGB") if isinstance(image, Image.Image) else Image.fromarray(np.asarray(image)).convert("RGB")
    w, h = img.size
    resized = img.resize((_INPUT_SIZE, _INPUT_SIZE), Image.BILINEAR)
    arr = np.asarray(resized, dtype=np.float32)
    arr = (arr - 127.5) / 128.0
    pixel = arr.transpose(2, 0, 1)[None].astype(np.float32)
    input_name = session.get_inputs()[0].name
    outputs = session.run(None, {input_name: pixel})
    boxes, scores = _decode(outputs)
    idx = np.where(scores >= threshold)[0]
    if idx.size == 0:
        return []
    boxes, scores = boxes[idx], scores[idx]
    keep = _nms(boxes, scores, _NMS_IOU)
    result = []
    for i in keep:
        x1, y1, x2, y2 = boxes[i]
        result.append((x1 / _INPUT_SIZE * w, y1 / _INPUT_SIZE * h, x2 / _INPUT_SIZE * w, y2 / _INPUT_SIZE * h, float(scores[i])))
    return result


def _cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def embed_face_crops(image: Any, boxes: list[tuple[float, float, float, float, float]], backend: Any) -> list[list[float]]:
    """Кропает лица + эмбеддит SigLIP2. Возвращает векторы (768-dim)."""
    from PIL import Image
    import tempfile

    img = image.convert("RGB") if isinstance(image, Image.Image) else Image.fromarray(np.asarray(image)).convert("RGB")
    vecs = []
    with tempfile.TemporaryDirectory() as td:
        for i, (x1, y1, x2, y2, _score) in enumerate(boxes):
            crop = img.crop((int(x1), int(y1), int(x2), int(y2)))
            crop = crop.resize((224, 224), Image.BILINEAR)
            p = Path(td) / f"face_{i}.jpg"
            crop.save(p, "JPEG")
            vecs.append(backend.embed_image(path=p, media_class="photo", scene_type=None))
    return vecs


def cluster_faces(vectors: list[list[float]], threshold: float = 0.55) -> list[list[int]]:
    """Жадная кластеризация по косинусу. Возвращает кластеры индексов."""
    clusters: list[list[int]] = []
    for i, v in enumerate(vectors):
        placed = False
        for cluster in clusters:
            centroid = np.mean([vectors[j] for j in cluster], axis=0)
            if _cos(v, centroid.tolist()) >= threshold:
                cluster.append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
    return clusters


__all__ = ["cluster_faces", "detect_faces", "embed_face_crops", "model_dir"]
