"""FR-2.14: точные дубли (несколько файлов одного content_hash) и почти-дубли."""

from __future__ import annotations

from kadr.ai.duplicates import compute_dup_groups

NOW = "2026-11-20T10:00:00.000+00:00"


def _asset(catalog, content_hash, taken_at, phash=None):
    cur = catalog.conn.execute(
        "INSERT INTO assets(content_hash, kind, bytes, taken_at, phash, created_at, updated_at) "
        "VALUES (?, 'photo', 1, ?, ?, ?, ?)",
        (content_hash, taken_at, phash, NOW, NOW),
    )
    return cur.lastrowid


def _file(catalog, volume_id, asset_id, rel_path):
    catalog.add_file(asset_id, volume_id, rel_path, ".jpg", 1, 1.0, is_primary=True)


def test_exact_duplicate_marks_multi_file_asset(catalog):
    volume_id = catalog.ensure_volume("/tmp/lib")
    dup = _asset(catalog, "a" * 64, "2026-01-01T00:00:00+00:00")
    unique = _asset(catalog, "b" * 64, "2026-01-02T00:00:00+00:00")
    _file(catalog, volume_id, dup, "a.jpg")
    _file(catalog, volume_id, dup, "copy.jpg")
    _file(catalog, volume_id, unique, "b.jpg")

    result = compute_dup_groups(catalog)
    assert result["exact_groups"] == 1
    assert result["exact_copies"] == 1

    assert catalog.asset_context(dup)["dup.group"] == "exact-" + "a" * 8
    assert catalog.asset_context(dup)["dup.copies"] == 1
    assert catalog.asset_context(dup)["dup.is_original"] is True
    assert catalog.asset_context(unique)["dup.group"] == "single"


def test_near_duplicates_go_to_review_only(catalog):
    a = _asset(catalog, "e" * 64, "2026-01-01T00:00:00+00:00", phash="0" * 16)
    b = _asset(catalog, "f" * 64, "2026-01-02T00:00:00+00:00", phash="0" * 14 + "11")
    result = compute_dup_groups(catalog)
    assert result["near_candidates"] == 1
    reviews = catalog.conn.execute(
        "SELECT asset_id FROM review_queue WHERE reason='near_duplicate'"
    ).fetchall()
    assert {int(r["asset_id"]) for r in reviews} == {b}
    assert catalog.asset_context(a)["dup.group"] == "single"


def test_near_scan_cap_skips_huge_archives(catalog):
    _asset(catalog, "e" * 64, "2026-01-01T00:00:00+00:00", phash="0" * 16)
    _asset(catalog, "f" * 64, "2026-01-02T00:00:00+00:00", phash="1" * 16)
    result = compute_dup_groups(catalog, near_cap=1)
    assert result["near_skipped"] is True
    assert result["near_candidates"] == 0
