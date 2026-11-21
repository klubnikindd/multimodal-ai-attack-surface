#!/usr/bin/env python3
"""Golden-датасет: метрики качества (v3.1 §13.2.1, FR-13.4).

Метрики:
- «по дате»        : доля файлов, где папка движка (YYYY/YYYY-MM-DD) = golden folder.
- «по дате+времени»: доля файлов, где taken_at (дата+время) = golden taken_at.
- «по источнику»   : доля файлов, где taken_at_source = golden source.
- precision@5 / nDCG@10 / CER : заглушки (нужны поисковые запросы + OCR-эталон).

Запуск:
  python tests/golden/eval_quality.py [--inputs data/golden/v1/inputs]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from kadr.config import Settings  # noqa: E402
from kadr.catalog.repository import Catalog  # noqa: E402
from kadr.scanner import scan_root  # noqa: E402


def _folder(dt: str | None) -> str | None:
    if not dt:
        return None
    d = dt[:10]
    return f"{d[:4]}/{d}" if len(d) == 10 else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", default=str(REPO / "data" / "golden" / "v1" / "inputs"))
    ap.add_argument("--labels", default=str(REPO / "tests" / "golden" / "labels.json"))
    ap.add_argument("--expected", default=str(REPO / "tests" / "golden" / "expected.json"))
    args = ap.parse_args()

    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    golden = {a["name"]: a for a in labels.get("assets", [])}

    print(f"golden: {len(golden)} файлов, vlm_prompt_rev={labels.get('vlm_prompt_rev')}")

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / ".kadr"
        settings = Settings(home=home, library_root=Path(tmp) / "library")
        catalog = Catalog(settings.catalog_path)
        report = scan_root(catalog, Path(args.inputs), settings)
        rows = catalog.list_assets(limit=100000)
        catalog.close()

    print(f"scan: {report.scanned} просканировано, {report.added_assets} активов, {len(report.errors)} ошибок")
    date_ok = date_total = time_ok = src_ok = src_total = 0
    mismatches: list[str] = []
    for row in rows:
        name = Path(row["path"]).name
        if name not in golden:
            continue
        g = golden[name]
        engine_folder = _folder(row.get("taken_at"))
        golden_folder = g.get("folder") or expected.get(name)
        date_total += 1
        if engine_folder == golden_folder:
            date_ok += 1
        elif len(mismatches) < 10:
            mismatches.append(f"{name}: движок={engine_folder} golden={golden_folder}")

        if (row.get("taken_at") or "")[:16] == (g.get("taken_at") or "")[:16]:
            time_ok += 1

        if g.get("taken_at_source") is not None:
            src_total += 1
            if row.get("taken_at_source") == g.get("taken_at_source"):
                src_ok += 1

    print("\n=== Метрики ===")
    print(f"по дате         : {date_ok}/{date_total} = {date_ok / date_total:.1%}" if date_total else "по дате: n/a")
    print(f"по дате+времени : {time_ok}/{date_total} = {time_ok / date_total:.1%}" if date_total else "")
    print(f"по источнику    : {src_ok}/{src_total} = {src_ok / src_total:.1%}" if src_total else "по источнику: n/a")
    print(f"precision@5     : not_measured (нужны запросы + релевантность)")
    print(f"nDCG@10         : not_measured (нужны запросы + релевантность)")
    print(f"CER             : not_measured (нужен OCR-эталон)")
    if mismatches:
        print("\n=== Расхождения (первые 10) ===")
        for m in mismatches:
            print(" ", m)

    # Порог §13.2.1: «по дате» >= 0.98 для прохождения гейта.
    ok = date_total > 0 and (date_ok / date_total) >= 0.98
    print(f"\n{'PASS' if ok else 'FAIL'}: по дате >= 0.98 (§13.2.1)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
