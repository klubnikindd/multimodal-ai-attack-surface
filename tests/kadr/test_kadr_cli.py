from __future__ import annotations

import json
import subprocess
import sys


def _run(tmp_path, *args):
    cmd = [sys.executable, "-m", "kadr", "--home", str(tmp_path / ".kadr"), "--json", *args]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return completed


def test_cli_scan_plan_apply_search_undo_and_doctor(tmp_path, source_dir):
    library = tmp_path / "library"
    scan = _run(tmp_path, "scan", str(source_dir))
    assert scan.returncode == 0, scan.stderr
    report = json.loads(scan.stdout)
    assert report["scanned"] >= 4

    analyze = _run(tmp_path, "analyze")
    assert analyze.returncode == 0, analyze.stderr

    plan = _run(tmp_path, "plan", "--preset", "date", "--root", str(library), "--tree")
    assert plan.returncode == 0, plan.stderr
    plan_payload = json.loads(plan.stdout)
    plan_id = plan_payload["plan_id"]
    assert plan_id > 0
    assert "Archive" in plan_payload["tree"]

    applied = _run(tmp_path, "apply", str(plan_id))
    assert applied.returncode == 0, applied.stderr
    applied_payload = json.loads(applied.stdout)
    assert applied_payload["completed"] > 0
    assert any(p.is_file() for p in library.rglob("*"))

    search = _run(tmp_path, "search", "kind:photo", "-k", "5")
    assert search.returncode == 0, search.stderr
    search_payload = json.loads(search.stdout)
    assert "index_badge" in search_payload

    undo = _run(tmp_path, "undo", str(plan_id))
    assert undo.returncode == 0, undo.stderr
    assert not any(p.is_file() for p in library.rglob("*"))

    doctor = _run(tmp_path, "doctor")
    assert doctor.returncode == 0, doctor.stderr
    doctor_payload = json.loads(doctor.stdout)
    assert doctor_payload["product"] == "Kadr"
    assert doctor_payload["vector_index"]["backend"]


def test_cli_init_and_rules_list(tmp_path):
    init = _run(tmp_path, "init")
    assert init.returncode == 0, init.stderr
    assert json.loads(init.stdout)["catalog"].endswith("catalog.db")
    rules = _run(tmp_path, "rules", "list")
    assert rules.returncode == 0, rules.stderr
    assert "date" in json.loads(rules.stdout)


def test_cli_doctor_text_output_without_closed_database(tmp_path):
    """Регресс спайка C: doctor печатал статус после catalog.close()."""
    init = _run(tmp_path, "init")
    assert init.returncode == 0, init.stderr
    cmd = [sys.executable, "-m", "kadr", "--home", str(tmp_path / ".kadr"), "doctor"]
    # encoding=utf-8: CLI печатает UTF-8, а локаль Windows — cp1251.
    completed = subprocess.run(  # noqa: S603
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    assert "FTS5:" in completed.stdout
    assert "Векторный индекс:" in completed.stdout
    assert "Cannot operate on a closed database" not in completed.stdout
