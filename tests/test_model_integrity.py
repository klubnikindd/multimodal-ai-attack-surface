"""model-integrity (§12.3): локальная и HF-ETag проверка — без сети в CI-тестах."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def module():
    spec = importlib.util.spec_from_file_location(
        "check_model_integrity", REPO_ROOT / "scripts" / "check-model-integrity.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sha256_file(module, tmp_path):
    path = tmp_path / "x.bin"
    path.write_bytes(b"kadr")
    assert module.sha256_file(path) == hashlib.sha256(b"kadr").hexdigest()


def test_hf_lfs_oid_uses_tree_api(module, monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'[{"path":"f.gguf","lfs":{"oid":"089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae","size":1107409952}}]'

    captured = {}

    def fake_urlopen(request, timeout):  # noqa: ARG001
        captured["url"] = request.get_full_url()
        assert "tree/" in request.get_full_url()
        return FakeResponse()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    oid, size = module.hf_lfs_oid("https://huggingface.co/X/Y", "rev", "f.gguf")
    assert oid == "089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae"
    assert size == 1107409952


def test_main_verifies_local_file_and_fails_on_mismatch(module, monkeypatch, tmp_path):
    (tmp_path / "models" / "m1").mkdir(parents=True)
    (tmp_path / "models" / "m1" / "w.bin").write_bytes(b"data")
    good = hashlib.sha256(b"data").hexdigest()
    manifest = {
        "models": [
            {
                "id": "m1",
                "enabled": True,
                "revision": "r",
                "license_source_url": "https://huggingface.co/X/Y",
                "files": [{"name": "w.bin", "sha256": good, "bytes": 4}],
            }
        ]
    }

    class FakeReport:
        issues: list[str] = []
        warnings: list[str] = []

    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "load_manifest", lambda: manifest)
    monkeypatch.setattr(module, "validate_manifest", lambda data: FakeReport())

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = module.main()
    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["checked"][0]["source"] == "local"

    # расхождение — fail-closed
    manifest["models"][0]["files"][0]["sha256"] = "0" * 64
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        code = module.main()
    assert code == 1
    assert "sha256 mismatch" in buf2.getvalue()
