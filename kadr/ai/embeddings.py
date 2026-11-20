"""Эмбеддинги: интерфейс бэкенда и оффлайн fallback без весов."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..catalog.repository import Catalog
from ..config import Settings
from ..errors import PathSafetyError
from ..security.paths import safe_join
from .concepts import feature_vector, image_features, text_features

CONCEPT_MODEL_ID = "concept-hash-v1"
CONCEPT_DIM = 256


class EmbeddingBackend(ABC):
    model_id: str = CONCEPT_MODEL_ID
    dim: int = CONCEPT_DIM
    available: bool = True
    reason: str | None = None

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        ...

    @abstractmethod
    def embed_image(
        self,
        *,
        path: str | Path | None,
        media_class: str,
        scene_type: str | None,
        ocr_text: str | None = None,
        tags: Sequence[str] = (),
        filename_tokens: str | None = None,
    ) -> list[float]:
        ...


class ConceptHashingEmbedding(EmbeddingBackend):
    """Детерминированный concept-hash энкодер (Lite fallback, не SigLIP)."""

    model_id = CONCEPT_MODEL_ID
    dim = CONCEPT_DIM

    def embed_text(self, text: str) -> list[float]:
        return feature_vector(text_features(text), dim=self.dim)

    def embed_image(
        self,
        *,
        path: str | Path | None,
        media_class: str,
        scene_type: str | None,
        ocr_text: str | None = None,
        tags: Sequence[str] = (),
        filename_tokens: str | None = None,
    ) -> list[float]:
        features = image_features(
            path=str(path) if path else None,
            media_class=media_class,
            scene_type=scene_type,
            ocr_text=ocr_text,
            tags=tags,
            filename_tokens=filename_tokens,
        )
        return feature_vector(features, dim=self.dim)


class OnnxEmbeddingBackend(EmbeddingBackend):
    """Слот для SigLIP 2/OpenCLIP ONNX.

    В v0.1 backend всегда ``pending``: даже при наличии файла весов нет
    подтверждённой ревизии/preprocessing, поэтому он не должен выглядеть как
    поддерживаемый. Production-путь появится после M0-аудита.
    """

    def __init__(self, model_path: str | Path | None, *, dim: int = 768) -> None:
        self.model_path = Path(model_path) if model_path else None
        self.dim = dim
        self.model_id = "siglip2-onnx-pending"
        self.available = False
        self.reason = "ONNX weights are not verified/installed; use concept-hash fallback until M0 license gate"
        if self.model_path and self.model_path.is_file():
            try:
                import onnxruntime  # noqa: F401

                self.available = False
                self.reason = "onnxruntime is present, but preprocessing/model revision is not pinned in this build"
            except Exception as exc:
                self.reason = f"onnxruntime unavailable: {exc}"

    def embed_text(self, text: str) -> list[float]:  # pragma: no cover - активируется после M0
        raise NotImplementedError(self.reason)

    def embed_image(self, **kwargs: Any) -> list[float]:  # pragma: no cover
        raise NotImplementedError(self.reason)


def get_embedding_backend(catalog: Catalog | None = None, settings: Settings | None = None) -> EmbeddingBackend:
    settings = settings or Settings()
    configured = catalog.get_setting("embedding_backend") if catalog is not None else None
    if configured == "onnx":
        backend = OnnxEmbeddingBackend(Path(settings.home) / "models" / "siglip2" / "model.onnx")
        if backend.available:
            return backend
    return ConceptHashingEmbedding()


def embed_asset(catalog: Catalog, asset_id: int, backend: EmbeddingBackend | None = None) -> list[float]:
    backend = backend or get_embedding_backend(catalog)
    asset = catalog.get_asset(asset_id)
    if asset is None:
        raise ValueError(f"asset {asset_id} not found")
    primary = catalog.primary_file(asset_id)
    if primary is None:
        raise ValueError(f"asset {asset_id} has no primary file")
    volume_root = catalog.volume_root(int(primary["volume_id"]))
    path = None
    if volume_root:
        try:
            path = safe_join(volume_root, primary["rel_path"])
        except PathSafetyError:
            path = None
    ocr_row = catalog.get_ai_output(asset_id, "ocr")
    ocr_text = ""
    if ocr_row:
        try:
            ocr_text = str(json.loads(ocr_row["output_json"]).get("text", ""))
        except (TypeError, ValueError):
            ocr_text = ""
    tags = [row["name"] for row in catalog.tags_for_asset(asset_id)]
    vector = backend.embed_image(
        path=path,
        media_class=str(asset["media_class"] or "unknown"),
        scene_type=asset["scene_type"],
        ocr_text=ocr_text,
        tags=tags,
        filename_tokens=Path(primary["rel_path"]).stem,
    )
    catalog.upsert_embedding(
        asset_id,
        backend.model_id,
        vector,
        index_generation=catalog.current_index_generation(backend.model_id),
    )
    return vector


def embed_query(text: str, backend: EmbeddingBackend | None = None) -> list[float]:
    return (backend or ConceptHashingEmbedding()).embed_text(text)


def embedding_dim(catalog: Catalog, model_id: str = CONCEPT_MODEL_ID) -> int:
    row = catalog.conn.execute(
        "SELECT dim FROM embeddings WHERE model_id=? ORDER BY id DESC LIMIT 1", (model_id,)
    ).fetchone()
    return int(row["dim"]) if row else CONCEPT_DIM


__all__ = [
    "CONCEPT_DIM",
    "CONCEPT_MODEL_ID",
    "ConceptHashingEmbedding",
    "EmbeddingBackend",
    "OnnxEmbeddingBackend",
    "embed_asset",
    "embed_query",
    "embedding_dim",
    "get_embedding_backend",
]
