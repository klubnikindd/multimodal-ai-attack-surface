from __future__ import annotations

from kadr.licensing import validate_manifest, validate_manifest_file


def _enabled_model(**overrides):
    model = {
        "id": "test-model",
        "role": "embeddings",
        "variant": "base",
        "revision": "a" * 40,
        "enabled": True,
        "files": [{"name": "model.onnx", "sha256": "b" * 64, "bytes": 100}],
        "license": "Apache-2.0",
        "license_source_url": "https://example.invalid/license",
        "license_verified_at": "2026-01-01",
        "commercial_use": True,
        "allowed_profiles": ["lite", "balanced", "pro"],
    }
    model.update(overrides)
    return model


def test_default_manifest_is_honest_and_passes():
    report = validate_manifest_file()
    assert report.ok
    # v0.1: включены верифицированные VLM + SigLIP2 embeddings (P3-01);
    # OCR (RapidOCR) остаётся disabled до фиксации ONNX-артефакта.
    assert report.enabled_count == 3
    enabled = [model for model in report.models if model["enabled"]]
    assert {model["role"] for model in enabled} == {"vlm", "embeddings"}
    assert all(model.get("license_verified_at") for model in enabled)
    assert all(model["commercial_use"] for model in enabled)


def test_unknown_license_fails_closed_for_enabled_model():
    report = validate_manifest({"models": [_enabled_model(license="unknown")]})
    assert report.ok is False
    assert any("fail-closed" in issue for issue in report.issues)


def test_noncommercial_and_agpl_are_rejected():
    for license_name in ("CC-BY-NC-4.0", "CC-BY-NC-SA", "AGPL-3.0", "research-only", "GPL-3.0"):
        report = validate_manifest({"models": [_enabled_model(license=license_name)]})
        assert report.ok is False, license_name


def test_vlm_must_be_instruct_thinking_off_and_not_lite():
    model = _enabled_model(
        role="vlm",
        variant="Thinking",
        thinking=True,
        max_output_tokens=384,
        image_max_side=1024,
        allowed_profiles=["lite", "pro"],
    )
    report = validate_manifest({"models": [model]})
    assert report.ok is False
    text = " ".join(report.issues)
    assert "Instruct" in text
    assert "thinking" in text
    assert "Lite" in text


def test_missing_sha_or_revision_fails_for_enabled_model():
    report = validate_manifest({"models": [_enabled_model(revision="main", files=[{"name": "x", "sha256": None}])]})
    assert report.ok is False
    assert any("revision" in issue for issue in report.issues)
    assert any("sha256" in issue for issue in report.issues)
