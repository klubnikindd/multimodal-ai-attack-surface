from __future__ import annotations

from pathlib import Path

import pytest

import kadr.executor as executor
from kadr.executor import apply_plan, cleanup_temp_files, undo_plan
from kadr.hashing import hash_file
from kadr.planner import build_plan
from kadr.profiles import detect_profile
from kadr.rules.dsl import load_rules_dict
from kadr.rules.presets import get_preset
from kadr.scanner import scan_root


def test_copy_failure_leaves_source_intact_and_journal_has_error(catalog, settings, source_dir, tmp_path, monkeypatch):
    scan_root(catalog, source_dir, settings)
    library = tmp_path / "library"
    plan = build_plan(catalog, load_rules_dict(get_preset("date")), library, profile=detect_profile("lite"))
    before = {p.name: hash_file(p) for p in source_dir.iterdir() if p.is_file()}

    def boom(src, dst, expected_hash):
        raise OSError("ENOSPC simulated")

    monkeypatch.setattr(executor, "_copy_verify", boom)
    report = apply_plan(catalog, plan.id, settings=settings)
    assert report.errors >= 1
    assert report.state == "applied_with_errors"
    after = {p.name: hash_file(p) for p in source_dir.iterdir() if p.is_file()}
    assert after == before
    journal = catalog.list_journal(plan.id)
    assert journal and journal[0]["error"] is not None
    assert journal[0]["done_at"] is None
    # undo не должен ничего ломать и не терять оригиналы
    undo = undo_plan(catalog, plan.id, settings=settings)
    assert undo.errors == 0
    assert {p.name: hash_file(p) for p in source_dir.iterdir() if p.is_file()} == before


def test_undo_refuses_changed_destination(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    library = tmp_path / "library"
    plan = build_plan(catalog, load_rules_dict(get_preset("date")), library, profile=detect_profile("lite"))
    report = apply_plan(catalog, plan.id, settings=settings)
    assert report.errors == 0
    copied = [p for p in library.rglob("*") if p.is_file()]
    assert copied
    copied[0].write_bytes(b"tampered after apply")
    undo = undo_plan(catalog, plan.id, settings=settings)
    assert undo.errors >= 1
    assert undo.state == "partially_undone"
    # Оригиналы всё ещё целы
    for path in source_dir.iterdir():
        if path.is_file():
            assert path.exists()


def test_keyboard_interrupt_marks_plan_interrupted_and_undo_works(catalog, settings, source_dir, tmp_path, monkeypatch):
    scan_root(catalog, source_dir, settings)
    library = tmp_path / "library"
    plan = build_plan(catalog, load_rules_dict(get_preset("date")), library, profile=detect_profile("lite"))
    before = {p.name: hash_file(p) for p in source_dir.iterdir() if p.is_file()}
    original = executor._copy_verify
    calls = {"n": 0}

    def flaky(src, dst, expected):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("user pressed Ctrl+C")
        return original(src, dst, expected)

    monkeypatch.setattr(executor, "_copy_verify", flaky)
    with pytest.raises(KeyboardInterrupt):
        apply_plan(catalog, plan.id, settings=settings)
    persisted = catalog.get_plan(plan.id)
    assert persisted is not None and persisted.state == "interrupted"
    copied = [p for p in library.rglob("*") if p.is_file()]
    assert len(copied) == 1
    undo = undo_plan(catalog, plan.id, settings=settings)
    assert undo.errors == 0
    assert not [p for p in library.rglob("*") if p.is_file()]
    assert {p.name: hash_file(p) for p in source_dir.iterdir() if p.is_file()} == before


def test_undo_recovers_write_ahead_row_without_done_at(catalog, settings, source_dir, tmp_path):
    scan_root(catalog, source_dir, settings)
    library = tmp_path / "library"
    plan = build_plan(catalog, load_rules_dict(get_preset("date")), library, profile=detect_profile("lite"))
    item = plan.items[0]
    # Имитируем kill -9 между созданием dst и mark_journal_done.
    dst = Path(item.dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(Path(item.src).read_bytes())
    catalog.set_plan_state(plan.id, "interrupted")
    catalog.add_journal(plan.id, item.seq, plan.mode, item.src, item.dst, item.hash)
    undo = undo_plan(catalog, plan.id, settings=settings)
    assert undo.errors == 0
    assert not dst.exists()


def test_stale_temp_files_are_cleaned(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    stale = root / ".photo.jpg.kadr-tmp-deadbeef"
    stale.write_bytes(b"partial")
    assert cleanup_temp_files(root, older_than_seconds=0) == 1
    assert not stale.exists()
