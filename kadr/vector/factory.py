"""Выбор векторного бэкенда по настройке и числу векторов (FR-3.13)."""

from __future__ import annotations

from ..catalog.repository import Catalog
from .ann_usearch import AnnVectorIndex
from .base import VectorIndex
from .brute_force import BruteForceVectorIndex
from .hybrid import HybridVectorIndex
from .sqlite_vec import SqliteVecIndex

DEFAULT_MODEL_ID = "concept-hash-v1"
# По спайку E (2026-11-20, docs/BENCHMARKS.md): sqlite-vec держит 100K и 200K
# (p95 141/122 мс при NFR-18 ≤300/800 мс) — brute-force-зона выше, чем
# опасался v3.
# p95 141 мс при цели NFR-18 ≤300 мс, а hybrid — пока точный Python-перебор
# (~450 мс уже на 10K) без FTS5-сужения. Поэтому автоматически выбирается
# sqlite-vec до 100K и tuned-ANN выше; hybrid включается только после M2
# (реализация FTS5/метаданные-сужения) и до тех пор не выбирается автоматически.
SQLITE_VEC_LIMIT = 200_000
BRUTE_FORCE_COMFORT_LIMIT = SQLITE_VEC_LIMIT
HYBRID_SWITCH_LIMIT = SQLITE_VEC_LIMIT


def auto_backend(vector_count: int) -> str:
    if vector_count > SQLITE_VEC_LIMIT:
        return "ann"
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
