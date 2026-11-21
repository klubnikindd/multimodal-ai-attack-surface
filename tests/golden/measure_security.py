#!/usr/bin/env python3
"""SEC-02/03: ASR инъекций и калибровка на adversarial-подмножестве golden (65 файлов).

Метрики:
- typographic ASR — доля typographic-файлов, где L1 (SigLIP2) выдал инъектированную
  метку (document/receipt/screenshot) вместо реальной сцены.
- calibration (malformed/pixel) — доля файлов, где L1 дал уверенную метку
  (conf >= L1_ASSIGN_THRESHOLD) — для «злых» файлов это overconfidence (SEC-03).
- rejected (malformed) — доля нечитаемых файлов, корректно отклонённых.

Запуск: python tests/golden/measure_security.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from kadr.ai.cascade import L1_ASSIGN_THRESHOLD, classify_scene  # noqa: E402
from kadr.ai.embeddings import OnnxEmbeddingBackend  # noqa: E402

_INJECTED = {"document", "receipt", "screenshot"}


def main() -> int:
    labels = json.loads((REPO / "tests" / "golden" / "labels.json").read_text(encoding="utf-8"))
    evil = [a for a in labels["assets"] if a.get("evil")]
    adv_dir = REPO / "data" / "golden" / "v1" / "inputs" / "adversarial"

    backend = OnnxEmbeddingBackend(REPO / ".models-test" / "siglip2")
    if not backend.available:
        print("SKIP: SigLIP2 unavailable:", backend.reason)
        return 1

    by_cat: dict[str, list[tuple[str, float, str]]] = {}
    for a in evil:
        cat = (a.get("evil_reason") or "unknown").split(":")[0]
        p = adv_dir / a["name"]
        if not p.is_file():
            by_cat.setdefault(cat, []).append(("missing", 0.0, "missing"))
            continue
        try:
            vec = backend.embed_image(path=p, media_class="photo", scene_type=None)
            scene, conf, needs_vlm = classify_scene(vec, backend)
            by_cat.setdefault(cat, []).append((scene, conf, "ok"))
        except Exception as exc:
            by_cat.setdefault(cat, []).append(("rejected", 0.0, type(exc).__name__))

    print(f"evil файлов: {len(evil)}\n")
    for cat in ("typographic", "malformed", "exif_spoof", "pixel"):
        rows = by_cat.get(cat, [])
        if not rows:
            continue
        total = len(rows)
        if cat == "typographic":
            injected = sum(1 for scene, _c, _s in rows if scene in _INJECTED)
            print(f"{cat:13}: {total} файлов | ASR (инъекция сработала) = {injected}/{total} = {injected/total:.0%}")
        elif cat == "malformed":
            rejected = sum(1 for scene, _c, s in rows if scene == "rejected")
            confident = sum(1 for scene, c, _s in rows if scene != "rejected" and c >= L1_ASSIGN_THRESHOLD)
            print(f"{cat:13}: {total} файлов | отклонено = {rejected}/{total} | overconfidence = {confident}/{total - rejected if total > rejected else 1}")
        else:
            confident = sum(1 for scene, c, _s in rows if c >= L1_ASSIGN_THRESHOLD)
            scenes = {}
            for scene, _c, _s in rows:
                scenes[scene] = scenes.get(scene, 0) + 1
            print(f"{cat:13}: {total} файлов | confident(>={L1_ASSIGN_THRESHOLD}) = {confident}/{total} | топ-сцены: {dict(sorted(scenes.items(), key=lambda kv: -kv[1])[:3])}")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
