#!/usr/bin/env python3
"""CI-гейт license-code (ТЗ v3.1 §12.1, §12.3, §12.4).

Проверяет лицензии установленных дистрибутивов против denylist и
licenses/allowlist.yaml, пишет SBOM в CycloneDX 1.5. Не требует сети.

Использование:
    python scripts/check-code-licenses.py --sbom sbom.cdx.json
    python scripts/check-code-licenses.py --allow-unknown   # только для локальной разработки
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import date, datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = REPO_ROOT / "licenses" / "allowlist.yaml"
FIRST_PARTY = {"kadr", "attack_surface"}

# §12.1: запрещено в пользовательской сборке.
DENIED_MARKERS: tuple[tuple[str, str], ...] = (
    ("AGPL", "AGPL"),
    ("AFFERO", "AGPL"),
    ("CC-BY-NC", "CC-BY-NC"),
    ("NON-COMMERCIAL", "non-commercial"),
    ("NONCOMMERCIAL", "non-commercial"),
    ("RESEARCH ONLY", "research-only"),
    ("RESEARCH-ONLY", "research-only"),
)
# GPL без «Lesser»: статическая линковка/распространение в пользовательской сборке.
GPL_MARKERS = ("GPL-2.0", "GPL-3.0", "GPLV2", "GPLV3", "GNU GENERAL PUBLIC LICENSE")
LESSER_MARKERS = ("LGPL", "LESSER GENERAL PUBLIC")
SCOPES = {"runtime", "build", "dev", "research"}
SPDX_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Apache-2.0", ("APACHE-2.0", "APACHE 2.0", "APACHE SOFTWARE LICENSE")),
    ("MIT", ("MIT LICENSE", "THE MIT LICENSE")),
    ("BSD-3-Clause", ("BSD 3-CLAUSE", "BSD-3-CLAUSE")),
    ("BSD-2-Clause", ("BSD 2-CLAUSE", "BSD-2-CLAUSE")),
    ("ISC", ("ISC LICENSE", "THE ISC LICENSE")),
    ("MPL-2.0", ("MPL-2.0", "MOZILLA PUBLIC LICENSE 2.0")),
    ("LGPL-2.1-only", ("LGPL-2.1", "LESSER GENERAL PUBLIC LICENSE")),
    ("PSF-2.0", ("PYTHON SOFTWARE FOUNDATION", "PSF-2.0")),
    ("Unlicense", ("THE UNLICENSE", "UNLICENSE")),
)


def _license_text(dist: metadata.Distribution) -> str:
    md = dist.metadata
    parts = [md.get("License-Expression") or "", md.get("License") or ""]
    for classifier in md.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            parts.append(classifier.split("::")[-1].strip())
    return " | ".join(part for part in parts if part and part.strip())


def _classify(text: str) -> str:
    """Возвращает метку: AGPL/CC-BY-NC/non-commercial/research-only/GPL/other/unknown."""
    upper = text.upper()
    if not upper.strip():
        return "unknown"
    for marker, label in DENIED_MARKERS:
        if marker in upper:
            return label
    if any(marker in upper for marker in LESSER_MARKERS):
        return "other"
    if any(marker in upper for marker in GPL_MARKERS):
        return "GPL"
    if upper.strip() in {"UNKNOWN", "NONE", "UNSPECIFIED", "NOASSERTION"}:
        return "unknown"
    return "other"


def _spdx(text: str) -> str:
    upper = text.upper()
    for spdx_id, hints in SPDX_HINTS:
        if spdx_id.upper() in upper or any(hint in upper for hint in hints):
            return spdx_id
    return ""


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _load_allowlist(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    problems: list[str] = []
    if not path.exists():
        return {}, [f"allowlist не найден: {path}"]
    try:
        import yaml
    except Exception:  # pragma: no cover
        return {}, ["PyYAML не установлен: allowlist не проверить"]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        return {}, ["allowlist: entries должен быть списком"]
    result: dict[str, dict[str, Any]] = {}
    today = date.today()
    for raw in entries:
        entry = dict(raw or {})
        component = str(entry.get("component") or "").strip()
        if "==" not in component:
            problems.append(f"allowlist: '{component}' должен быть 'name==version'")
            continue
        for field in ("license", "reason", "scope", "expires_at", "issue"):
            if not entry.get(field):
                problems.append(f"allowlist: у '{component}' нет поля {field}")
        if entry.get("scope") not in SCOPES:
            problems.append(f"allowlist: у '{component}' scope не из {sorted(SCOPES)}")
        expires = _parse_date(entry.get("expires_at"))
        if expires is None:
            problems.append(f"allowlist: у '{component}' некорректный expires_at")
        elif expires < today:
            problems.append(f"allowlist: у '{component}' истёк срок ({expires.isoformat()})")
        elif (expires - today).days > 366:
            problems.append(f"allowlist: у '{component}' срок больше 12 месяцев")
        result[component.lower()] = entry
    return result, problems


def _collect() -> list[tuple[str, str, str]]:
    rows: dict[str, tuple[str, str, str]] = {}
    for dist in metadata.distributions():
        name = str(dist.metadata.get("Name") or "").strip()
        if not name or name.lower() in FIRST_PARTY:
            continue
        version = str(dist.version or "").strip()
        rows.setdefault(f"{name.lower()}=={version}", (name, version, _license_text(dist)))
    return sorted(rows.values(), key=lambda item: item[0].lower())


def _cyclonedx(rows: list[tuple[str, str, str]], *, tool: str = "kadr-license-gate") -> dict[str, Any]:
    components = []
    for name, version, text in rows:
        spdx = _spdx(text) or _classify(text)
        license_entry: dict[str, Any]
        if spdx in {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "MPL-2.0",
                    "LGPL-2.1-only", "PSF-2.0", "Unlicense"}:
            license_entry = {"license": {"id": spdx}}
        else:
            license_entry = {"license": {"name": spdx}}
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{version}",
                "licenses": [license_entry],
            }
        )
    namespace = uuid.uuid5(uuid.NAMESPACE_URL, "kadr-sbom")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid5(namespace, str(len(components)))}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "tools": [{"name": tool, "version": "1"}],
        },
        "components": components,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="license-code gate (ТЗ v3.1 §12.1–12.4)")
    parser.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    parser.add_argument("--sbom", default=None, help="куда записать CycloneDX SBOM")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--allow-unknown",
        action="store_true",
        help="не падать на неизвестной лицензии (только локальная разработка)",
    )
    args = parser.parse_args(argv)

    allowlist, problems = _load_allowlist(Path(args.allowlist))
    rows = _collect()
    violations: list[dict[str, str]] = []
    for name, version, text in rows:
        component = f"{name.lower()}=={version}"
        label = _classify(text)
        if component in allowlist:
            entry = allowlist[component]
            if entry.get("scope") == "research":
                violations.append(
                    {
                        "component": f"{name}=={version}",
                        "kind": "allowlist-scope",
                        "detail": "scope=research не покрывает пользовательскую сборку",
                        "license": label,
                    }
                )
            continue
        if label in {"AGPL", "CC-BY-NC", "non-commercial", "research-only", "GPL"}:
            violations.append(
                {
                    "component": f"{name}=={version}",
                    "kind": "denied",
                    "detail": f"лицензия {label} запрещена для пользовательского профиля",
                    "license": label,
                }
            )
        elif label == "unknown" and not args.allow_unknown:
            violations.append(
                {
                    "component": f"{name}=={version}",
                    "kind": "unknown",
                    "detail": "лицензия не определена — fail-closed (§12.4)",
                    "license": "unknown",
                }
            )

    if args.sbom:
        Path(args.sbom).write_text(
            json.dumps(_cyclonedx(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    payload = {
        "ok": not violations and not problems,
        "components": len(rows),
        "allowlist_entries": len(allowlist),
        "sbom": args.sbom,
        "violations": violations,
        "allowlist_problems": problems,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"компонентов: {len(rows)}; allowlist: {len(allowlist)}; SBOM: {args.sbom or '—'}")
        for item in problems:
            print(f"FAIL(allowlist): {item}")
        for item in violations:
            print(f"FAIL({item['kind']}): {item['component']} — {item['detail']}")
        print("OK" if payload["ok"] else f"нарушений: {len(violations) + len(problems)}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
