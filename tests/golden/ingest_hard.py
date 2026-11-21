#!/usr/bin/env python3
"""Ингест Fooocus-файлов как «трудных» (hard) в golden-датасет (v3.1 §13.2.1)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "data" / "golden" / "v1" / "target_raw" / "fooocus"
MANIFEST = REPO / "tests" / "golden" / "manifest.json"
LABELS = REPO / "tests" / "golden" / "labels.json"
EXPECTED = REPO / "tests" / "golden" / "expected.json"

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


def _blake3(b: bytes) -> str:
    import blake3
    return blake3.blake3(b).hexdigest()


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _date_from_name(name: str) -> tuple[str, str] | None:
    m = _DATE_RE.match(name)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}", f"{y}/{y}-{mo}-{d}"


def main() -> None:
    files = sorted([f for f in SRC.iterdir() if f.is_file()])
    if not files:
        raise SystemExit("нет файлов в target_raw/fooocus/")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    existing = {a["name"] for a in labels["assets"]}

    added = 0
    for f in files:
        if f.name in existing:
            continue
        b = f.read_bytes()
        ext = f.suffix.lstrip(".").lower()
        kind = "photo" if ext in ("jpg", "jpeg", "png", "webp") else "other"
        dt, folder = _date_from_name(f.name) or (None, "_Unsorted")
        manifest["files"].append({
            "name": f.name,
            "bytes": len(b),
            "blake3": _blake3(b),
            "sha256": _sha256(b),
            "ext": ext,
            "kind": kind,
            "make": None,
            "model": None,
            "taken_at": dt,
            "taken_at_source": "filename" if dt else None,
            "source": "fooocus-generated",
            "license": "generated",
        })
        labels["assets"].append({
            "name": f.name,
            "kind": kind,
            "camera": None,
            "taken_at": dt,
            "taken_at_source": "filename" if dt else None,
            "folder": folder,
            "scene": None,
            "event": None,
            "quality": [],
            "duplicate": {"group": "single", "copies": 0, "is_original": True, "near_dup_base": None, "near_dup_copies": None},
            "evil": False,
            "hard": True,
            "hard_reason": "fooocus-generated (grainy/ambiguous)",
        })
        expected[f.name] = folder
        added += 1

    labels["hard_note"] = f"hard (fooocus) файлы: {added} добавлено из target_raw/fooocus/"
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    EXPECTED.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total = len(labels["assets"])
    hard = sum(1 for a in labels["assets"] if a.get("hard"))
    evil = sum(1 for a in labels["assets"] if a.get("evil"))
    print(f"добавлено hard: {added} | total={total} | hard={hard} ({hard/total*100:.1f}%) | evil={evil} ({evil/total*100:.2f}%)")


if __name__ == "__main__":
    main()
