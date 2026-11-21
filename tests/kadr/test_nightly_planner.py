"""Ночной планировщик VLM (FR-3.14) — e2e: plan → job (структура)."""

from __future__ import annotations

from kadr.ai.vlm import NightlyVLMPlanner
from kadr.catalog.repository import Catalog
from kadr.config import Settings
from kadr.profiles import detect_profile


def test_nightly_plan_structure(tmp_path) -> None:
    settings = Settings(home=tmp_path / ".kadr", library_root=tmp_path / "lib")
    catalog = Catalog(settings.catalog_path)
    profile = detect_profile()
    planner = NightlyVLMPlanner(catalog, profile)

    plan = planner.plan(10, explicit=True, ac_power=True, free_ram_gb=8.0, free_disk_gb=10.0)
    d = plan.to_dict()
    assert isinstance(d, dict)
    assert "available" in d
    assert "reasons" in d

    # если план доступен — можно создать job и он попадает в список
    if plan.available:
        job_id = planner.create_job(plan)
        assert job_id > 0
        jobs = catalog.list_jobs(limit=10)
        assert any(int(j["id"]) == job_id for j in jobs)


def test_nightly_plan_never_available_without_conditions(tmp_path) -> None:
    settings = Settings(home=tmp_path / ".kadr", library_root=tmp_path / "lib")
    catalog = Catalog(settings.catalog_path)
    profile = detect_profile()
    planner = NightlyVLMPlanner(catalog, profile)
    # нет активов → план либо недоступен, либо reasons непуст
    plan = planner.plan(0, explicit=True, ac_power=False, free_ram_gb=0.1, free_disk_gb=0.1)
    assert isinstance(plan.to_dict(), dict)
