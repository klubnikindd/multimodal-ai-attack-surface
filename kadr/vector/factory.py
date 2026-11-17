"""Выбор векторного бэкенда по настройке и числу векторов (FR-3.13)."""

from __future__ import annotations

from ..catalog.repository import Catalog
from .ann_usearch import AnnVectorIndex
from .base import VectorIndex
from .brute_force import BruteForceVectorIndex
from .hybrid import HybridVectorIndex
from .sqlite_vec import SqliteVecIndex

DEFAULT_MODEL_ID = "concept-hash-v1"
BRUTE_FORCE_COMFORT_LIMIT = 100_000
HYBRID_SWITCH_LIMIT = 50_000


def auto_backend(vector_count: int) -> str:
    if vector_count > BRUTE_FORCE_COMFORT_LIMIT:
        return "ann"
    if vector_count > HYBRID_SWITCH_LIMIT:
        return "hybrid"
    return "sqlite-vec"


def create_vector_index(
    catalog: Catalog,
    *,
    backend: str | None = None,
    model_id: str = DEFAULT_MODEL_ID,
    index_generation: int | None = None,
    dim: int = 256,
) -> VectorIndex:
    if backend is None:
        backend = catalog.get_setting("vector_backend_override")
    generation = index_generation or catalog.current_index_generation(model_id)
    count = catalog.count_embeddings(model_id, generation)
    if not backend:
        backend = auto_backend(count)
    backend = str(backend).lower()
    common = {
        "catalog": catalog,
        "model_id": model_id,
        "index_generation": generation,
        "dim": dim,
    }
    if backend in {"sqlite-vec", "sqlite_vec", "sqlitevec"}:
        return SqliteVecIndex(**common)
    if backend in {"ann", "usearch", "hnsw"}:
        return AnnVectorIndex(**common)
    if backend == "hybrid":
        return HybridVectorIndex(**common)
    if backend in {"brute-force", "brute_force", "exact"}:
        return BruteForceVectorIndex(**common)
    # Неизвестный override не должен тихо ломать поиск: явная деградация.
    index = BruteForceVectorIndex(**common)
    index.record_status(
        status="degraded", reason=f"unknown vector backend {backend!r}; exact fallback"
    )
    return index


__all__ = [
    "BRUTE_FORCE_COMFORT_LIMIT",
    "DEFAULT_MODEL_ID",
    "HYBRID_SWITCH_LIMIT",
    "auto_backend",
    "create_vector_index",
]
