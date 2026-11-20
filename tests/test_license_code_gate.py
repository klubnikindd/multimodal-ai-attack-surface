"""Гейт license-code (§12.1–12.4 v3.1): SBOM, denylist, allowlist."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_gate():
    path = REPO_ROOT / "scripts" / "check-code-licenses.py"
    spec = importlib.util.spec_from_file_location("check_code_licenses", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


def test_classification_of_denied_and_allowed(gate):
    assert gate._classify("GNU AFFERO GENERAL PUBLIC LICENSE") == "AGPL"
    assert gate._classify("GPL-3.0-only") == "GPL"
    assert gate._classify("CC-BY-NC-4.0") == "CC-BY-NC"
    assert gate._classify("Research-only weights") == "research-only"
    # LGPL (ffmpeg) разрешён: динамическая линковка, не GPL-статическая.
    assert gate._classify("LGPL-2.1-only") == "other"
    assert gate._classify("") == "unknown"
    assert gate._classify("MIT License") == "other"


def test_spdx_detection(gate):
    assert gate._spdx("Apache Software License 2.0") == "Apache-2.0"
    assert gate._spdx("The MIT License") == "MIT"
    assert gate._spdx("BSD-3-Clause") == "BSD-3-Clause"
    assert gate._spdx("почесать") == ""


def test_allowlist_schema_is_strict(gate, tmp_path):
    path = tmp_path / "allowlist.yaml"
    path.write_text(
        "schema_version: 1\nentries:\n"
        "  - component: pkg   # без ==version\n"
        "  - component: x==1.0\n    license: MIT\n    reason: r\n"
        "    scope: research\n    expires_at: 2000-01-01\n    issue: https://example/1\n"
        "  - component: y==2.0\n    license: MIT\n    reason: r\n"
        "    scope: prod\n    expires_at: 2027-06-01\n    issue: https://example/2\n"
        "  - component: z==3.0\n    scope: dev\n"
        "    expires_at: 2027-06-01\n    issue: https://example/3\n",
        encoding="utf-8",
    )
    entries, problems = gate._load_allowlist(path)
    assert isinstance(entries, dict)
    assert any("name==version" in item for item in problems)
    assert any("истёк" in item for item in problems)
    assert any("scope не из" in item for item in problems)
    assert any("нет поля" in item for item in problems)


def test_research_scope_does_not_cover_user_build(gate, tmp_path, monkeypatch):
    rows = gate._collect()
    assert rows, "в окружении нет дистрибутивов"
    name, version, _text = rows[0]
    allowlist = tmp_path / "allowlist.yaml"
    allowlist.write_text(
        "schema_version: 1\nentries:\n"
        f"  - component: {name.lower()}=={version}\n"
        "    license: MIT\n    reason: r\n    scope: research\n"
        "    expires_at: 2027-06-01\n    issue: https://example/1\n",
        encoding="utf-8",
    )
    code = gate.main(["--allowlist", str(allowlist), "--json"])
    assert code == 1


def test_sbom_is_valid_cyclonedx(gate, tmp_path):
    out = tmp_path / "sbom.json"
    assert gate.main(["--sbom", str(out), "--allow-unknown"]) == 0
    bom = json.loads(out.read_text(encoding="utf-8"))
    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.5"
    assert bom["components"]
    first = bom["components"][0]
    assert {"type", "name", "version", "purl", "licenses"} <= set(first)
