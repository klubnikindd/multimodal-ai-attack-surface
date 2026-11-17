from __future__ import annotations

import pytest

import kadr.ai.classify as classify
from kadr.ai.classify import OcrResult, analyze_catalog, classify_asset
from kadr.config import Settings
from kadr.errors import ConfigError
from kadr.scanner import scan_root


def _scan_sample(tmp_path, catalog, settings) -> int:
    from PIL import Image

    Image.new("RGB", (24, 18), color=(40, 80, 120)).save(tmp_path / "sample.jpg")
    report = scan_root(catalog, tmp_path, settings)
    return int(catalog.get_file_by_path(report.volume_id, "sample.jpg")["asset_id"])


def test_classify_reports_actual_ocr_availability(tmp_path, catalog, settings, monkeypatch):
    asset_id = _scan_sample(tmp_path, catalog, settings)

    monkeypatch.setattr(
        classify, "run_ocr", lambda path, sandbox=True, lang=None: OcrResult(available=False)
    )
    result = classify_asset(catalog, asset_id)
    assert result["ocr_available"] is False
    assert result["ocr_chars"] == 0


def test_classify_reports_true_ocr_when_engine_available(tmp_path, catalog, settings, monkeypatch):
    asset_id = _scan_sample(tmp_path, catalog, settings)

    monkeypatch.setattr(
        classify,
        "run_ocr",
        lambda path, sandbox=True, lang=None: OcrResult(
            text="hello", engine="tesseract", available=True
        ),
    )
    result = classify_asset(catalog, asset_id)
    assert result["ocr_available"] is True
    assert result["ocr_chars"] == 5


def test_ocr_language_is_passed_to_engine(tmp_path, catalog, settings, monkeypatch):
    asset_id = _scan_sample(tmp_path, catalog, settings)
    seen: dict[str, object] = {}

    def fake_ocr(path, *, sandbox=False, lang=None):
        seen["sandbox"] = sandbox
        seen["lang"] = lang
        return OcrResult(text="текст", engine="tesseract", available=True)

    monkeypatch.setattr(classify, "run_ocr", fake_ocr)
    classify_asset(catalog, asset_id, ocr_language="rus+eng")
    assert seen == {"sandbox": True, "lang": "rus+eng"}


def test_ocr_language_defaults_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("KADR_OCR_LANG", "deu+eng")
    configured = Settings.from_env(home=tmp_path / ".kadr")
    assert configured.ocr_language == "deu+eng"
    assert Settings(home=tmp_path / ".kadr-default").ocr_language == "rus+eng"
    with pytest.raises(ConfigError):
        Settings(home=tmp_path / ".kadr-bad", ocr_language="-l")


def test_analyze_catalog_propagates_run_ocr_enabled(tmp_path, catalog, settings, monkeypatch):
    _scan_sample(tmp_path, catalog, settings)
    calls = []

    def fake_ocr(path, **kwargs):
        calls.append(path)
        return OcrResult(text="hello", engine="tesseract", available=True)

    monkeypatch.setattr(classify, "run_ocr", fake_ocr)
    result = analyze_catalog(catalog, run_ocr_enabled=False)
    assert calls == []
    assert result["processed"] == 1
