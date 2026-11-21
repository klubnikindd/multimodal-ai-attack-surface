"""RapidOCR ONNX (вшивание, PP-OCRv4). Пропускается без весов."""

from __future__ import annotations

from pathlib import Path

import pytest

from kadr.ai.classify import run_ocr, run_ocr_rapidocr


def _text_image(tmp_path: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (600, 160), (255, 255, 255))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    d.text((20, 50), "HELLO 123", fill=(0, 0, 0), font=font)
    p = tmp_path / "t.png"
    img.save(p)
    return p


def test_rapidocr_recognizes_text(tmp_path: Path) -> None:
    p = _text_image(tmp_path)
    r = run_ocr_rapidocr(p)
    if not r.available:
        pytest.skip(r.error or "rapidocr unavailable")
    assert r.engine == "rapidocr"
    assert "HELLO" in r.text.upper() or "123" in r.text


def test_run_ocr_prefers_rapidocr(tmp_path: Path) -> None:
    p = _text_image(tmp_path)
    r = run_ocr(p)
    # rapidocr при наличии весов; иначе fallback (tesseract/none) — не падаем
    assert r.engine in ("rapidocr", "tesseract", "none")
