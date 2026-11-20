from __future__ import annotations

from pathlib import Path

from kadr.ai.classify import analyze_catalog
from kadr.executor import apply_plan, undo_plan
from kadr.hashing import hash_file
from kadr.planner import build_plan, plan_tree, render_tree
from kadr.profiles import detect_profile
from kadr.rules.dsl import load_rules_dict
from kadr.rules.presets import get_preset
from kadr.scanner import find_exact_duplicates, group_paths, scan_root
from kadr.search import execute_search


def test_scan_groups_raw_jpeg_livephoto_and_sidecar(tmp_path, catalog, settings):
    root = tmp_path / "src"
    root.mkdir()
    (root / "IMG_0001.CR2").write_bytes(b"raw-data")
    (root / "IMG_0001.JPG").write_bytes(b"jpeg-data")
    (root / "IMG_0001.xmp").write_text("<xmp/>", encoding="utf-8")
    (root / "IMG_0002.HEIC").write_bytes(b"heic")
    (root / "IMG_0002.MOV").write_bytes(b"mov")
    (root / "IMG_0003.LRV").write_bytes(b"lrv")
    (root / "IMG_0003.MP4").write_bytes(b"mp4")
    groups = group_paths(list(root.iterdir()), root)
    assert len(groups) == 3
    assert sorted(len(group) for group in groups) == [2, 2, 3]

    report = scan_root(catalog, root, settings)
    assert report.groups == 3
    assert report.errors == []
    assert catalog.stats()["files"] == 7


def test_scan_is_idempotent(catalog, settings, source_dir):
    first = scan_root(catalog, source_dir, settings)
    second = scan_root(catalog, source_dir, settings)
    assert first.added_assets == 3
    assert second.added_assets == 0
    assert second.added_files == 0
    assert second.skipped_unchanged >= 4
    assert catalog.stats()["assets"] == 3


def test_plan_dry_run_does_not_write_and_tree_is_built(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    library = tmp_path / "library"
    plan = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        library,
        profile=detect_profile("lite"),
        case_insensitive=False,
    )
    assert plan.state == "draft"
    assert plan.summary["items"] >= 4
    assert plan.summary["mode"] == "copy"
    assert not any(p.is_file() for p in library.rglob("*"))
    tree = plan_tree(plan)
    assert tree["children"]
    assert "Archive" in render_tree(plan)
    assert plan.id > 0
    assert catalog.get_plan(plan.id) is not None


def test_apply_copy_verifies_and_undo_keeps_originals(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    analyze_catalog(catalog)
    original_hashes = {
        path.name: hash_file(path) for path in source_dir.iterdir() if path.is_file()
    }
    library = tmp_path / "library"
    plan = build_plan(
        catalog, load_rules_dict(get_preset("date")), library, profile=detect_profile("lite")
    )
    report = apply_plan(catalog, plan.id, settings=settings)
    assert report.errors == 0
    assert report.completed == plan.summary["items"]
    copied = [p for p in library.rglob("*") if p.is_file()]
    assert len(copied) == report.completed
    for path in source_dir.iterdir():
        if path.is_file():
            assert hash_file(path) == original_hashes[path.name]
    undo = undo_plan(catalog, plan.id, settings=settings)
    assert undo.errors == 0
    assert not [p for p in library.rglob("*") if p.is_file()]
    for path in source_dir.iterdir():
        if path.is_file():
            assert path.exists()
            assert hash_file(path) == original_hashes[path.name]


def test_collision_suffix_and_move_mode(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    library = tmp_path / "library"
    # Создаём коллизию с исходным именем в целевом каталоге.
    target_dir = library / "Archive" / "2024" / "2024-07-15" / "photo"
    target_dir.mkdir(parents=True)
    (target_dir / "IMG_20240715_132200.jpg").write_bytes(b"existing")

    rules = get_preset("date")
    rules["safety"]["mode"] = "move"
    plan = build_plan(catalog, load_rules_dict(rules), library, profile=detect_profile("lite"))
    moved = [item for item in plan.items if item.src.endswith("IMG_20240715_132200.jpg")]
    assert moved and moved[0].collision is True
    assert "(" in Path(moved[0].dst).name
    report = apply_plan(catalog, plan.id, settings=settings)
    assert report.errors == 0
    assert not (source_dir / "IMG_20240715_132200.jpg").exists()
    assert not (source_dir / "IMG_20240716_142200.jpg").exists()


def test_search_structural_fts_and_semantic_status(catalog, settings, source_dir):
    scan_root(catalog, source_dir, settings)
    analyze_catalog(catalog)
    result = execute_search(catalog, "camera:Canon kind:photo", k=10)
    assert result.hits
    assert all(hit.fields["kind"] == "photo" for hit in result.hits)
    assert "семантический поиск" in result.index_badge or "ограничен" in result.index_badge
    semantic = execute_search(catalog, "закат пляж", k=10)
    assert isinstance(semantic.hits, list)
    assert semantic.status.backend in {"sqlite-vec", "brute-force", "hybrid", "ann"}


def test_search_date_range_and_rating_syntax(catalog, settings, source_dir):
    scan_root(catalog, source_dir, settings)
    result = execute_search(catalog, "date:2024-07-15 kind:photo", k=10)
    assert result.hits
    assert all(str(hit.fields["taken_at"]).startswith("2024-07-15") for hit in result.hits)
    empty = execute_search(catalog, "date:2030-01-01 kind:photo", k=10)
    assert not empty.hits


def test_unsupported_format_goes_to_unsorted_and_review(tmp_path, catalog, settings):
    root = tmp_path / "src"
    root.mkdir()
    (root / "old.bmp").write_bytes(b"BM-not-supported")
    scan_root(catalog, root, settings)
    library = tmp_path / "library"
    plan = build_plan(
        catalog, load_rules_dict(get_preset("date")), library, profile=detect_profile("lite")
    )
    assert plan.items
    assert all("_Unsorted" in item.dst for item in plan.items)
    assert catalog.list_review()


def test_wedding_preset_does_not_block_lite_and_marks_unavailable(
    catalog, settings, source_dir, tmp_path
):
    scan_root(catalog, source_dir, settings)
    plan = build_plan(
        catalog,
        load_rules_dict(get_preset("wedding")),
        tmp_path / "library",
        profile=detect_profile("lite"),
    )
    assert "jobs" in plan.summary["unavailable_rules"]
    assert plan.items
    assert plan.summary["errors"] == []


def test_exact_duplicates_share_asset(tmp_path, catalog, settings):
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"same-bytes")
    (root / "b.jpg").write_bytes(b"same-bytes")
    scan_root(catalog, root, settings)
    duplicates = find_exact_duplicates(catalog)
    assert len(duplicates) == 1
    assert sorted(next(iter(duplicates.values()))) == ["a.jpg", "b.jpg"]


def test_structural_filter_is_hard_not_union_with_fts(catalog, settings, source_dir, tmp_path):
    from PIL import Image

    nikon = source_dir / "nikon_beach.jpg"
    exif = Image.Exif()
    exif[36867] = "2024:07:16 10:00:00"
    exif[271] = "Nikon"
    exif[272] = "Z6"
    Image.new("RGB", (80, 60), color=(20, 120, 160)).save(nikon, exif=exif)
    report = scan_root(catalog, source_dir, settings)
    file_row = catalog.get_file_by_path(report.volume_id, "nikon_beach.jpg")
    assert file_row is not None
    catalog.fts_upsert(int(file_row["asset_id"]), caption="beach", tags="beach")
    result = execute_search(catalog, "camera:Canon beach", k=20)
    assert all("Nikon" not in hit.fields["camera"] for hit in result.hits)


def test_scan_survives_file_disappearing_between_walk_and_stat(
    catalog, settings, source_dir, monkeypatch
):
    import kadr.scanner as scanner

    original = scanner.group_paths

    def deleting(paths, root):
        groups = original(paths, root)
        victim = source_dir / "IMG_20240716_142200.jpg"
        if victim.exists():
            victim.unlink()
        return groups

    monkeypatch.setattr(scanner, "group_paths", deleting)
    report = scan_root(catalog, source_dir, settings)
    assert report.errors
    assert report.scanned >= 1


def test_rule_tags_are_persisted_on_apply(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    plan = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library",
        profile=detect_profile("lite"),
    )
    screenshot_items = [item for item in plan.items if "Screenshot" in item.src]
    assert screenshot_items and screenshot_items[0].tags
    report = apply_plan(catalog, plan.id, settings=settings)
    assert report.errors == 0
    tags = catalog.tags_for_asset(screenshot_items[0].asset_id)
    assert any(tag["name"] == "скриншот" and tag["source"] == "rule" for tag in tags)


def test_plan_limit_counts_assets_not_enumerate_index(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    full = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library-full",
        profile=detect_profile("lite"),
    )
    limited = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library-limited",
        profile=detect_profile("lite"),
        limit=2,
    )
    assert full.summary["assets"] == 3
    assert limited.summary["assets"] == 2
    assert len(limited.items) < len(full.items)
    zero = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library-zero",
        profile=detect_profile("lite"),
        limit=0,
    )
    assert zero.items == []


def test_plan_with_partial_space_reviews_only_excess(
    catalog, settings, source_dir, tmp_path, monkeypatch
):
    from collections import namedtuple

    import kadr.planner as planner

    DiskUsage = namedtuple("DiskUsage", "total used free")
    scan_root(catalog, source_dir, settings)
    # Сначала строим план без ограничения, чтобы узнать размер первого элемента.
    normal = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library-normal",
        profile=detect_profile("lite"),
    )
    free_for_one = normal.items[0].size_bytes
    monkeypatch.setattr(planner.shutil, "disk_usage", lambda path: DiskUsage(1, 1, free_for_one))
    plan = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library",
        profile=detect_profile("lite"),
    )
    states = [item.state for item in plan.items]
    assert "pending" in states
    assert "review" in states
    assert any(
        item.review_reason == "insufficient_space" for item in plan.items if item.state == "review"
    )


def test_plan_with_insufficient_space_is_review_not_apply(
    catalog, settings, source_dir, tmp_path, monkeypatch
):
    from collections import namedtuple

    import kadr.planner as planner

    DiskUsage = namedtuple("DiskUsage", "total used free")
    monkeypatch.setattr(planner.shutil, "disk_usage", lambda path: DiskUsage(1, 1, 0))
    scan_root(catalog, source_dir, settings)
    plan = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "library",
        profile=detect_profile("lite"),
    )
    assert plan.summary["space_ok"] is False
    assert plan.items
    assert all(
        item.state == "review" and item.review_reason == "insufficient_space" for item in plan.items
    )
    assert any(row["reason"] == "plan_insufficient_space" for row in catalog.list_review())
    report = apply_plan(catalog, plan.id, settings=settings)
    assert report.errors == 0
    assert report.completed == 0
    assert report.skipped == len(plan.items)
    assert not [path for path in (tmp_path / "library").rglob("*") if path.is_file()]


def test_plan_counter_uses_first_item_of_asset(catalog, settings, tmp_path):
    source = tmp_path / "counter-src"
    source.mkdir()
    (source / "A.CR2").write_bytes(b"raw-a")
    (source / "A.JPG").write_bytes(b"jpeg-a")
    (source / "B.jpg").write_bytes(b"jpeg-b")
    scan_root(catalog, source, settings)
    rules = {
        "version": 1,
        "rules": [{"id": "all", "then": {"path": "ByCounter/{counter}"}}],
        "fallback": {"path": "_Unsorted"},
    }
    plan = build_plan(
        catalog,
        load_rules_dict(rules),
        tmp_path / "counter-lib",
        profile=detect_profile("lite"),
    )
    dirs: dict[int, set[str]] = {}
    for item in plan.items:
        dirs.setdefault(item.asset_id, set()).add(Path(item.dst).parent.name)
    # Два ассета: A (RAW+JPEG) и B. Файлы одного ассета не разъезжаются,
    # а counter равен номеру первого элемента ассета в плане (1 и 3).
    assert len(dirs) == 2
    assert all(len(names) == 1 for names in dirs.values())
    assert sorted(next(iter(names)) for names in dirs.values()) == ["1", "3"]


def test_plan_preview_without_persist_has_zero_id(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    plan = build_plan(
        catalog,
        load_rules_dict(get_preset("date")),
        tmp_path / "preview-library",
        profile=detect_profile("lite"),
        persist=False,
    )
    assert plan.id == 0
    assert plan.items
    assert catalog.get_plan(0) is None


def test_catalog_rejects_unsafe_migration_identifier(catalog):
    import pytest

    from kadr.errors import CatalogError

    with pytest.raises(CatalogError):
        catalog._ensure_column("plan_items; DROP TABLE plans", "hash", "TEXT")
    with pytest.raises(CatalogError):
        catalog._ensure_column("plan_items", "hash", "TEXT; DROP TABLE plans")


def test_case_insensitive_collision_resolution():
    """FR-2.9: на case-insensitive ФС (Windows/macOS) регистрово-иные имена коллизуют."""
    from kadr.planner import _planned_key, resolve_collision

    dst = Path("Archive") / "Photo.jpg"
    planned = {_planned_key(Path("Archive") / "photo.jpg", case_insensitive=True)}
    resolved, collision, _reason = resolve_collision(
        dst, mode="suffix", source_hash="blake3:abc", planned=planned, case_insensitive=True
    )
    assert collision is True
    assert resolved.name.lower().startswith("photo (")  # suffix, а не перезапись


def test_case_sensitive_fs_does_not_collide_on_case():
    """На Linux (case-sensitive) Photo.jpg и photo.jpg — разные пути."""
    from kadr.planner import resolve_collision

    dst = Path("Archive") / "Photo.jpg"
    resolved, collision, _reason = resolve_collision(
        dst,
        mode="suffix",
        source_hash="blake3:abc",
        planned={"archive/photo.jpg"},
        case_insensitive=False,
    )
    assert collision is False
    assert resolved == dst
