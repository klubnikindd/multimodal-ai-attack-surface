from __future__ import annotations

from kadr.models import MediaMetadata
from kadr.vector.brute_force import BruteForceVectorIndex
from kadr.vector.factory import auto_backend, create_vector_index
from kadr.vector.hybrid import HybridVectorIndex
from kadr.vector.sqlite_vec import SqliteVecIndex


def _vector(index: int, dim: int = 8):
    return [1.0 if i == index % dim else 0.0 for i in range(dim)]


def _assets(catalog, count: int = 5):
    for index in range(1, count + 1):
        catalog.upsert_asset(f"hash-{index}", MediaMetadata(kind="photo"), size_bytes=1)


def test_brute_force_add_search_remove(catalog):
    _assets(catalog)
    index = BruteForceVectorIndex(catalog, model_id="test", dim=8)
    index.add(1, _vector(0))
    index.add(2, _vector(1))
    index.add(3, _vector(1))
    results = index.search(_vector(1), k=2)
    assert [asset_id for asset_id, _ in results] == [2, 3]
    assert index.count() == 3
    index.remove(2)
    assert index.count() == 2
    results = index.search(_vector(1), k=1)
    assert [asset_id for asset_id, _ in results] == [3]


def test_candidate_narrowing(catalog):
    _assets(catalog)
    index = BruteForceVectorIndex(catalog, model_id="test", dim=8)
    index.add(1, _vector(0))
    index.add(2, _vector(1))
    results = index.search(_vector(0), k=5, candidate_ids={2})
    assert [asset_id for asset_id, _ in results] == [2]


def test_sqlite_vec_fallback_is_visible(catalog):
    _assets(catalog)
    index = SqliteVecIndex(catalog, model_id="test", dim=8)
    index.add(1, _vector(0))
    results = index.search(_vector(0), k=1)
    status = index.status()
    assert results
    if not index.available:
        assert status.status == "degraded"
        assert status.reason
        assert status.degradation_visible is True


def test_hybrid_and_auto_backend(catalog):
    _assets(catalog)
    assert auto_backend(10) == "sqlite-vec"
    assert auto_backend(60_000) == "sqlite-vec"  # hybrid не авто до M2 (спайк E)
    assert auto_backend(150_000) == "sqlite-vec"  # 200K держит exact (122 мс)
    assert auto_backend(300_000) == "ann"
    index = create_vector_index(catalog, backend="hybrid", model_id="test", dim=8)
    assert isinstance(index, HybridVectorIndex)
    index.add(1, _vector(0))
    assert index.search(_vector(0), k=1)


def test_index_generation_keeps_embeddings_source(catalog):
    _assets(catalog)
    index = BruteForceVectorIndex(catalog, model_id="test", dim=8, index_generation=1)
    index.add(1, _vector(0))
    assert catalog.count_embeddings("test", 1) == 1
    index.rebuild()
    assert catalog.count_embeddings("test", 1) == 1
    assert index.count() == 1


def test_sqlite_vec_candidate_filter_uses_exact_fallback(catalog, monkeypatch):
    _assets(catalog)
    index = SqliteVecIndex(catalog, model_id="test", dim=8)
    index.add(1, _vector(0))
    index.add(2, _vector(1))
    index.available = True
    monkeypatch.setattr(index, "_search_sql", lambda payload, k, candidates: [])
    monkeypatch.setattr(index, "count", lambda: 2)
    results = index.search(_vector(0), k=1, candidate_ids={2})
    assert [asset_id for asset_id, _ in results] == [2]


def test_sqlite_vec_real_candidate_filter(catalog):
    _assets(catalog)
    index = SqliteVecIndex(catalog, model_id="test-real", dim=8)
    if not index.available:
        import pytest

        pytest.skip("sqlite-vec not installed")
    index.add(1, _vector(0))
    index.add(2, _vector(1))
    results = index.search(_vector(0), k=1, candidate_ids={2})
    assert [asset_id for asset_id, _ in results] == [2]


def test_ann_batches_large_sets_and_skips_empty():
    from kadr.vector.ann_usearch import _add_in_batches

    class FakeIndex:
        def __init__(self):
            self.batches = []

        def add(self, keys, vectors):
            self.batches.append((list(keys), list(vectors)))

    index = FakeIndex()
    _add_in_batches(index, [], [])
    assert index.batches == []

    keys = list(range(2500))
    vectors = [[float(key)] for key in keys]
    _add_in_batches(index, keys, vectors)
    assert [len(batch[0]) for batch in index.batches] == [1000, 1000, 500]
    assert index.batches[0][0][0] == 0
    assert index.batches[-1][0][-1] == 2499
    assert index.batches[0][1][0] == [0.0]


def test_ann_rebuild_deduplicates_asset_ids(catalog, monkeypatch):
    from kadr.vector.ann_usearch import AnnVectorIndex
    from kadr.vector.base import pack_vector

    class FakeIndex:
        def __init__(self, *, ndim, metric, connectivity, **kwargs):  # noqa: ARG002
            self.keys = []

        def add(self, keys, vectors):
            self.keys.extend(keys)

    index = AnnVectorIndex(catalog, model_id="test", dim=2)
    index._index_cls = FakeIndex
    monkeypatch.setattr(
        catalog,
        "get_embeddings",
        lambda *args, **kwargs: [
            {"asset_id": 1, "dim": 2, "vec": pack_vector([1.0, 0.0])},
            {"asset_id": 1, "dim": 2, "vec": pack_vector([0.0, 1.0])},
            {"asset_id": 2, "dim": 2, "vec": pack_vector([1.0, 1.0])},
        ],
    )
    index._rebuild_from_db()
    assert sorted(index._index.keys) == [1, 2]


def test_sqlite_vec_backend_is_active_when_extension_installed(catalog):
    """BLK-01 / FR-3.13: если расширение установлено, бэкенд ОБЯЗАН быть активен.

    Регресс: раньше `sqlite_vec.load()` вызывался без `enable_load_extension(True)`,
    расширение молча не грузилось ("not authorized"), и симантический поиск
    деградировал даже на машинах, где sqlite-vec установлен.
    """
    import pytest

    pytest.importorskip("sqlite_vec", reason="sqlite-vec не установлен")
    _assets(catalog)
    index = SqliteVecIndex(catalog, model_id="test-active", dim=8)
    assert index.available is True, index.reason
    status = index.status()
    assert status.status == "active", status.reason
    assert status.degradation_visible is False  # active — не деградация (FR-3.13)
    index.add(1, _vector(0))
    assert [asset_id for asset_id, _ in index.search(_vector(0), k=1)] == [1]
