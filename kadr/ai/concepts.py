"""Детерминированный Lite-энкодер понятий (fallback без весов моделей).

Это не CLIP и не замена ONNX-модели: слой даёт работающий оффлайн поиск и
zero-shot теги для M0/M2-каркаса, пока манифест весов не подтверждён. Любая
метрика качества на этом энкодере помечается как fallback, а не как SigLIP.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)
TILE = 32

CONCEPT_TOKENS: dict[str, tuple[str, ...]] = {
    "wedding": ("wedding", "bride", "groom", "ceremony", "свадьба", "невеста", "жених", "кольца"),
    "concert": ("concert", "stage", "band", "концерт", "сцена", "музыкант"),
    "sport": ("sport", "football", "run", "match", "спорт", "футбол", "бег", "матч"),
    "portrait": ("portrait", "face", "people", "портрет", "лицо", "люди", "человек"),
    "document": ("document", "scan", "paper", "invoice", "receipt", "документ", "скан", "чек", "квитанция", "бумага"),
    "screenshot": ("screenshot", "screen", "ui", "скриншот", "экран", "интерфейс"),
    "whiteboard": ("whiteboard", "board", "marker", "доска", "маркер"),
    "beach": ("beach", "sea", "ocean", "sand", "пляж", "море", "океан", "песок"),
    "mountain": ("mountain", "hike", "горы", "гора", "поход"),
    "city": ("city", "street", "building", "город", "улица", "здание"),
    "nature": ("nature", "forest", "tree", "природа", "лес", "дерево"),
    "sunset": ("sunset", "sunrise", "golden hour", "закат", "рассвет", "золотой час"),
    "night": ("night", "dark", "neon", "ночь", "темно", "неон"),
    "indoor": ("indoor", "room", "home", "inside", "дом", "комната", "помещение"),
    "outdoor": ("outdoor", "outside", "улица", "на улице", "снаружи"),
    "food": ("food", "meal", "dish", "еда", "блюдо", "обед", "ужин"),
    "product": ("product", "packshot", "товар", "продукт"),
    "car": ("car", "auto", "vehicle", "машина", "авто", "автомобиль"),
    "animal": ("dog", "cat", "animal", "собака", "кот", "кошка", "животное"),
    "travel": ("travel", "trip", "путешествие", "поездка"),
    "family": ("family", "child", "семья", "ребенок", "дети"),
    "snow": ("snow", "winter", "снег", "зима"),
    "rain": ("rain", "umbrella", "дождь", "зонт"),
}

ZERO_SHOT_DICTIONARY: dict[str, tuple[str, ...]] = {
    "документ": CONCEPT_TOKENS["document"],
    "скриншот": CONCEPT_TOKENS["screenshot"],
    "свадьба": CONCEPT_TOKENS["wedding"],
    "концерт": CONCEPT_TOKENS["concert"],
    "спорт": CONCEPT_TOKENS["sport"],
    "портрет": CONCEPT_TOKENS["portrait"],
    "пляж": CONCEPT_TOKENS["beach"],
    "горы": CONCEPT_TOKENS["mountain"],
    "город": CONCEPT_TOKENS["city"],
    "природа": CONCEPT_TOKENS["nature"],
    "закат": CONCEPT_TOKENS["sunset"],
    "ночь": CONCEPT_TOKENS["night"],
    "помещение": CONCEPT_TOKENS["indoor"],
    "улица": CONCEPT_TOKENS["outdoor"],
    "еда": CONCEPT_TOKENS["food"],
    "товар": CONCEPT_TOKENS["product"],
    "авто": CONCEPT_TOKENS["car"],
    "животные": CONCEPT_TOKENS["animal"],
    "путешествие": CONCEPT_TOKENS["travel"],
    "семья": CONCEPT_TOKENS["family"],
    "зима": CONCEPT_TOKENS["snow"],
    "дождь": CONCEPT_TOKENS["rain"],
}


@dataclass(slots=True)
class ImageStats:
    brightness: float
    saturation: float
    warmth: float
    aspect: float
    edge_density: float
    dominant: tuple[int, int, int]


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = unicodedata.normalize("NFKC", str(text)).lower()
    tokens = []
    for token in TOKEN_RE.findall(normalized):
        if len(token) >= 2:
            tokens.append(token)
        # Простая нормализация русских окончаний для словарных совпадений.
        if len(token) >= 5 and token[-1] in "аеиоуыэюя":
            tokens.append(token[:-1])
    return tokens


def image_stats(path: str | None) -> ImageStats | None:
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return None
    if not path:
        return None
    try:
        with Image.open(path) as img:
            width, height = img.size
            small = img.convert("RGB").resize((TILE, TILE), Image.Resampling.BILINEAR)
            pixels = list(small.getdata())
    except Exception:
        return None
    total = [0.0, 0.0, 0.0]
    saturations = []
    for r, g, b in pixels:
        total[0] += r
        total[1] += g
        total[2] += b
        maximum, minimum = max(r, g, b), min(r, g, b)
        saturations.append(0.0 if maximum == 0 else (maximum - minimum) / maximum)
    count = max(1, len(pixels))
    rgb = (int(total[0] / count), int(total[1] / count), int(total[2] / count))
    brightness = (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0
    saturation = sum(saturations) / count
    warmth = (rgb[0] - rgb[2]) / 255.0
    # Грубая оценка детализации: разница соседних пикселей.
    edges = 0
    for y in range(0, TILE, 2):
        for x in range(0, TILE - 1, 2):
            a = pixels[y * TILE + x]
            b = pixels[y * TILE + x + 1]
            edges += abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])
    edge_density = edges / ((TILE // 2) ** 2 * 3 * 255)
    aspect = width / height if height else 1.0
    return ImageStats(
        brightness=brightness,
        saturation=saturation,
        warmth=warmth,
        aspect=aspect,
        edge_density=edge_density,
        dominant=rgb,
    )


def image_concept_scores(stats: ImageStats | None, media_class: str, scene_type: str | None) -> dict[str, float]:
    scores: dict[str, float] = {}
    if stats is None:
        return scores
    if stats.brightness < 0.28:
        scores["night"] = 0.8
    if stats.brightness > 0.7:
        scores["bright"] = 0.7
    if stats.warmth > 0.12 and 0.25 < stats.brightness < 0.65:
        scores["sunset"] = min(0.9, 0.45 + stats.warmth)
    if stats.saturation < 0.12 and stats.edge_density > 0.15:
        scores["document"] = 0.55
    if stats.aspect < 0.9:
        scores["portrait"] = 0.55
    if stats.aspect > 1.25:
        scores["landscape"] = 0.55
    if media_class == "screenshot":
        scores["screenshot"] = 0.95
    if media_class == "document" or scene_type == "document":
        scores["document"] = 0.95
    if media_class == "photo" and stats.saturation > 0.2:
        scores["photo"] = 0.6
    return scores


def text_concept_scores(text: str | None) -> dict[str, float]:
    tokens = set(tokenize(text))
    scores: dict[str, float] = {}
    for concept, synonyms in CONCEPT_TOKENS.items():
        hits = sum(1 for synonym in synonyms if set(tokenize(synonym)) & tokens)
        if hits:
            scores[concept] = min(1.0, 0.45 + 0.2 * hits)
    return scores


def _feature_hash(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    index = value % dim
    sign = 1.0 if (value >> 63) & 1 else -1.0
    return index, sign


def feature_vector(features: dict[str, float], *, dim: int = 256) -> list[float]:
    vector = [0.0] * dim
    for feature, weight in features.items():
        index, sign = _feature_hash(feature, dim)
        vector[index] += sign * float(weight)
    norm = math.sqrt(sum(value * value for value in vector))
    if norm > 1e-12:
        vector = [value / norm for value in vector]
    return vector


def text_features(text: str | None, *, prefix: str = "tok") -> dict[str, float]:
    features: dict[str, float] = {}
    for token in tokenize(text):
        features[f"{prefix}:{token}"] = features.get(f"{prefix}:{token}", 0.0) + 1.0
    for concept, score in text_concept_scores(text).items():
        features[f"concept:{concept}"] = max(features.get(f"concept:{concept}", 0.0), score)
    return features


def image_features(
    *,
    path: str | None,
    media_class: str,
    scene_type: str | None,
    ocr_text: str | None = None,
    tags: Iterable[str] = (),
    filename_tokens: str | None = None,
) -> dict[str, float]:
    stats = image_stats(path)
    features: dict[str, float] = {}
    if stats is not None:
        features["visual:brightness"] = stats.brightness
        features["visual:saturation"] = stats.saturation
        features["visual:warmth"] = stats.warmth
        features["visual:aspect"] = min(stats.aspect, 4.0) / 4.0
        features["visual:edges"] = stats.edge_density
        features["visual:red"] = stats.dominant[0] / 255.0
        features["visual:green"] = stats.dominant[1] / 255.0
        features["visual:blue"] = stats.dominant[2] / 255.0
    features[f"class:{media_class}"] = 1.0
    if scene_type:
        features[f"scene:{scene_type}"] = 1.0
    for concept, score in image_concept_scores(stats, media_class, scene_type).items():
        features[f"concept:{concept}"] = max(features.get(f"concept:{concept}", 0.0), score)
    for token in tokenize(ocr_text):
        features[f"tok:{token}"] = features.get(f"tok:{token}", 0.0) + 1.2
    for tag in tags:
        for token in tokenize(tag):
            features[f"tok:{token}"] = features.get(f"tok:{token}", 0.0) + 0.8
    for token in tokenize(filename_tokens):
        features[f"tok:{token}"] = features.get(f"tok:{token}", 0.0) + 0.3
    return features


def zero_shot_tags(text: str | None, *, threshold: float = 0.5) -> list[tuple[str, float]]:
    tokens = set(tokenize(text))
    result: list[tuple[str, float]] = []
    for tag, synonyms in ZERO_SHOT_DICTIONARY.items():
        hits = sum(1 for synonym in synonyms if set(tokenize(synonym)) & tokens)
        if not hits:
            continue
        confidence = min(0.99, 0.5 + 0.15 * hits)
        if confidence >= threshold:
            result.append((tag, round(confidence, 2)))
    return sorted(result, key=lambda pair: pair[1], reverse=True)


__all__ = [
    "CONCEPT_TOKENS",
    "ZERO_SHOT_DICTIONARY",
    "ImageStats",
    "feature_vector",
    "image_concept_scores",
    "image_features",
    "image_stats",
    "text_concept_scores",
    "text_features",
    "tokenize",
    "zero_shot_tags",
]
