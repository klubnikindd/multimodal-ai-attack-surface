"""FR-1.7: скан пишет прогресс в jobs (для экрана Jobs и возобновления)."""

from __future__ import annotations

from kadr.scanner import scan_root


def test_scan_creates_and_finalizes_job(catalog, settings, source_dir):
    scan_root(catalog, source_dir, settings)
    jobs = catalog.list_jobs()
    scan_jobs = [dict(j) for j in jobs if j["kind"] == "scan"]
    assert scan_jobs
    job = scan_jobs[-1]
    assert job["state"] == "done"
    assert job["progress"] == 1.0
    assert job["total"] >= 3
    assert job["finished_at"]
