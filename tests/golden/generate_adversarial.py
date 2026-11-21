#!/usr/bin/env python3
"""Генерация adversarial-подмножества golden-датасета (v3.1 §13.2.1).

Из базы в inputs/Target/ генерирует ~36 «злых» файлов в inputs/adversarial/
(malformed / typographic / exif_spoof / pixel) и вливает их в
manifest.json + labels.json + expected.json.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import piexif
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
TARGET = REPO / "data" / "golden" / "v1" / "target_raw"
OUT = REPO / "data" / "golden" / "v1" / "inputs" / "adversarial"
MANIFEST = REPO / "tests" / "golden" / "manifest.json"
LABELS = REPO / "tests" / "golden" / "labels.json"
EXPECTED = REPO / "tests" / "golden" / "expected.json"

_TEXTS = ["RECEIPT 123", "ЧЕК 456", "DOCUMENT", "PASSPORT", "СВАДЬБА", "CONTRACT", "SCREENSHOT", "INVOICE 789", "ДОГОВОР"]


def _blake3(b: bytes) -> str:
    import blake3
    return blake3.blake3(b).hexdigest()


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _bases() -> list[Path]:
    return sorted(
        f for f in TARGET.iterdir()
        if f.is_file() and f.suffix.lower() in (".jpg", ".png", ".webp")
    )


def _to_rgb(img: Image.Image) -> Image.Image:
    return img.convert("RGB") if img.mode != "RGB" else img


def _font(size: int):
    for name in ("arial.ttf", "arialbd.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def gen_malformed(bases: list[Path]) -> list[dict]:
    entries: list[dict] = []
    data = []
    for i in range(3):
        p = bases[i]
        b = p.read_bytes()
        data.append(("trunc", b[: max(1, len(b) * 60 // 100)]))
    for i in range(3, 6):
        b = bases[i].read_bytes()
        data.append(("wrongext", b))
    data.append(("zero", b""))
    for i in range(6, 8):
        b = bytearray(bases[i].read_bytes())
        for j in range(min(8, len(b))):
            b[j] ^= 0xFF
        data.append(("corrupt", bytes(b)))
    for n, (why, payload) in enumerate(data, 1):
        ext = {"trunc": ".jpg", "zero": ".jpg", "corrupt": ".jpg"}.get(why, "")
        if why == "wrongext":
            ext = [".png", ".gif", ".txt"][n - 4]
        name = f"adv_malformed_{n:02d}{ext}"
        (OUT / name).write_bytes(payload)
        entries.append({"name": name, "why": why})
    return entries


def gen_typographic(bases: list[Path]) -> list[dict]:
    entries = []
    for i in range(9):
        p = bases[(i + 8) % len(bases)]
        img = _to_rgb(Image.open(p))
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 340, 110], fill=(255, 255, 255))
        draw.text((20, 20), _TEXTS[i], fill=(0, 0, 0), font=_font(40))
        name = f"adv_typo_{i + 1:02d}.jpg"
        img.save(OUT / name, "JPEG", quality=92)
        entries.append({"name": name, "why": _TEXTS[i]})
    return entries


def gen_exif_spoof(bases: list[Path]) -> list[dict]:
    entries = []
    fake_dates = ["1900:01:01 00:00:00", "2099:12:31 23:59:59", "1999:06:15 12:00:00"]
    for i in range(9):
        p = bases[(i + 17) % len(bases)]
        img = _to_rgb(Image.open(p))
        dt = fake_dates[i % len(fake_dates)]
        exif = {
            "0th": {
                piexif.ImageIFD.Make: b"FAKE-CAM",
                piexif.ImageIFD.Model: b"FAKE-MODEL",
                piexif.ImageIFD.DateTime: dt,
            },
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: dt,
                piexif.ExifIFD.DateTimeDigitized: dt,
            },
            "GPS": {
                piexif.GPSIFD.GPSLatitudeRef: "N",
                piexif.GPSIFD.GPSLatitude: ((0, 1), (0, 1), (0, 1)),
                piexif.GPSIFD.GPSLongitudeRef: "W",
                piexif.GPSIFD.GPSLongitude: ((0, 1), (0, 1), (0, 1)),
            },
        }
        name = f"adv_exif_{i + 1:02d}.jpg"
        img.save(OUT / name, "JPEG", quality=92, exif=piexif.dump(exif))
        entries.append({"name": name, "why": dt})
    return entries


def gen_pixel(bases: list[Path]) -> list[dict]:
    import numpy as np

    entries = []
    for i in range(9):
        p = bases[(i + 26) % len(bases)]
        img = _to_rgb(Image.open(p))
        arr = np.asarray(img, dtype=np.float32)
        rng = np.random.default_rng(1000 + i)
        noise = rng.normal(0, 35, arr.shape)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        name = f"adv_pixel_{i + 1:02d}.jpg"
        Image.fromarray(arr).save(OUT / name, "JPEG", quality=92)
        entries.append({"name": name, "why": f"gauss-noise-sigma35-seed{1000 + i}"})
    return entries


def _folder_for(dt: str | None) -> str:
    if not dt:
        return "_Unsorted"
    d = dt[:10].replace(":", "-")
    return f"{d[:4]}/{d}"


def build_entries() -> None:
    bases = _bases()
    if len(bases) < 9:
        raise SystemExit(f"нужно >=9 базовых фото, есть {len(bases)}")
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.iterdir():
        if f.is_file():
            f.unlink()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    labels = json.loads(LABELS.read_text(encoding="utf-8"))
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    existing = {a["name"] for a in labels["assets"]}

    generated = []
    generated += [(e, "malformed") for e in gen_malformed(bases)]
    generated += [(e, "typographic") for e in gen_typographic(bases)]
    generated += [(e, "exif_spoof") for e in gen_exif_spoof(bases)]
    generated += [(e, "pixel") for e in gen_pixel(bases)]

    for e, cat in generated:
        name = e["name"]
        if name in existing:
            continue
        path = OUT / name
        b = path.read_bytes()
        ext = path.suffix.lstrip(".").lower()
        try:
            with Image.open(path) as im:
                im.verify()
            kind = "photo" if ext in ("jpg", "jpeg", "png", "webp") else "other"
        except Exception:
            kind = "other"  # malformed/нечитаемый
        manifest["files"].append({
            "name": name,
            "bytes": len(b),
            "blake3": _blake3(b),
            "sha256": _sha256(b),
            "ext": ext,
            "kind": kind,
            "make": "FAKE-CAM" if cat == "exif_spoof" else None,
            "model": "FAKE-MODEL" if cat == "exif_spoof" else None,
            "taken_at": None,
            "taken_at_source": None,
            "source": "adversarial-generated",
            "license": "personal-derivative",
        })
        labels["assets"].append({
            "name": name,
            "kind": kind,
            "camera": None,
            "taken_at": None,
            "taken_at_source": None,
            "folder": "_Unsorted",
            "scene": None,
            "event": None,
            "quality": [],
            "duplicate": {"group": "single", "copies": 0, "is_original": True, "near_dup_base": None, "near_dup_copies": None},
            "evil": True,
            "evil_reason": f"{cat}: {e['why']}",
            "hard": cat in ("typographic", "pixel"),
        })
        expected[name] = "_Unsorted"

    labels["adversarial_note"] = f"adversarial (evil) файлы: {len(generated)} сгенерированы из inputs/Target/"
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LABELS.write_text(json.dumps(labels, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    EXPECTED.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"сгенерировано {len(generated)} adversarial файлов; assets={len(labels['assets'])}, files={len(manifest['files'])}")


if __name__ == "__main__":
    build_entries()
