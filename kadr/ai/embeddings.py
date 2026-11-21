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
SIGLIP2_MODEL_ID = "siglip2-base-patch16-224-int8"
SIGLIP2_DIM = 768
_SIGLIP2_MAX_TOKENS = 64


def _l2_normalize(vector: Sequence[float]) -> list[float]:
    import math

    norm = math.sqrt(sum(x * x for x in vector))
    if norm <= 0:
        return [float(x) for x in vector]
    return [float(x) / norm for x in vector]


def _resolve_siglip2_dir(settings: Settings) -> Path | None:
    import os

    candidates: list[Path] = []
    env = os.environ.get("KADR_SIGLIP2_DIR")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(settings.home) / "models" / "siglip2")
    candidates.append(Path(__file__).resolve().parents[2] / ".models-test" / "siglip2")
    for candidate in candidates:
        if (candidate / "text_model.onnx").is_file() and (candidate / "vision_model.onnx").is_file():
            return candidate
    return None


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
    """SigLIP 2 (int8 ONNX): текст ↔ изображение → 768-dim L2-нормализованный вектор.

    Текст: tokenizers (SentencePiece), EOS + pad до 64 токенов.
    Изображение: resize 224×224 (bicubic), rescale 1/255, normalize (x−0.5)/0.5
    по preprocessor_config.json.
    """

    model_id = SIGLIP2_MODEL_ID
    dim = SIGLIP2_DIM

    def __init__(self, model_dir: str | Path, *, dim: int = SIGLIP2_DIM) -> None:
        self.model_dir = Path(model_dir)
        self.dim = dim
        self.available = False
        self.reason: str | None = "SigLIP2 ONNX не установлен"
        self._text: Any = None
        self._vision: Any = None
        self._tokenizer: Any = None
        self._text_input: str | None = None
        self._vision_input: str | None = None
        text_path = self.model_dir / "text_model.onnx"
        vision_path = self.model_dir / "vision_model.onnx"
        tokenizer_path = self.model_dir / "tokenizer.json"
        if not (text_path.is_file() and vision_path.is_file() and tokenizer_path.is_file()):
            self.reason = f"SigLIP2 ONNX отсутствует в {self.model_dir}"
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._text = ort.InferenceSession(str(text_path), providers=["CPUExecutionProvider"])
            self._vision = ort.InferenceSession(str(vision_path), providers=["CPUExecutionProvider"])
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._text_input = self._text.get_inputs()[0].name
            self._vision_input = self._vision.get_inputs()[0].name
            self.available = True
            self.reason = None
        except Exception as exc:
            self.reason = f"SigLIP2 ONNX init: {exc}"

    def _pool(self, session: Any, input_name: str, arr: Any) -> list[float]:
        out = session.run([session.get_outputs()[-1].name], {input_name: arr})[0]
        return _l2_normalize([float(x) for x in out.reshape(-1)])

    def embed_text(self, text: str) -> list[float]:
        if not self.available:
            raise RuntimeError(self.reason or "SigLIP2 недоступен")
        import numpy as np

        ids = self._tokenizer.encode(text, add_special_tokens=True).ids[:_SIGLIP2_MAX_TOKENS]
        arr = np.asarray([ids], dtype=np.int64)
        return self._pool(self._text, self._text_input or "", arr)

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
        if not self.available:
            raise RuntimeError(self.reason or "SigLIP2 недоступен")
        import numpy as np
        from PIL import Image

        if not path:
            raise ValueError("SigLIP2 требует путь к изображению")
        img = Image.open(str(path)).convert("RGB")
        img = img.resize((224, 224), Image.BICUBIC)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5
        pixel = arr.transpose(2, 0, 1)[None].astype(np.float32)
        return self._pool(self._vision, self._vision_input or "", pixel)


def get_embedding_backend(catalog: Catalog | None = None, settings: Settings | None = None) -> EmbeddingBackend:
    settings = settings or Settings()
    configured = catalog.get_setting("embedding_backend") if catalog is not None else None
    if configured == "onnx":
        model_dir = _resolve_siglip2_dir(settings)
        if model_dir is not None:
            backend = OnnxEmbeddingBackend(model_dir)
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
    "SIGLIP2_DIM",
    "SIGLIP2_MODEL_ID",
    "ConceptHashingEmbedding",
    "EmbeddingBackend",
    "OnnxEmbeddingBackend",
    "embed_asset",
    "embed_query",
    "embedding_dim",
    "get_embedding_backend",
]
