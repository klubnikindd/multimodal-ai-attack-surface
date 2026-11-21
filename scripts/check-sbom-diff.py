#!/usr/bin/env python3
"""SBOM-diff (§12.3): сравнивает два CycloneDX SBOM (base vs pr).

Выводит добавленные/удалённые/изменённые компоненты для ревью PR.
Не является самостоятельным fail-гейтом — строгий гейт лицензий делает
check-code-licenses.py (AGPL/неизвестная лицензия = падение).
"""

from __future__ import annotations

import argparse
import json


def _components(path: str) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        str(c.get("name")): str(c.get("version", "?"))
        for c in data.get("components", [])
        if c.get("name")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--pr", required=True)
    args = parser.parse_args()

    base = _components(args.base)
    pr = _components(args.pr)

    added = {k: v for k, v in pr.items() if k not in base}
    removed = {k: v for k, v in base.items() if k not in pr}
    changed = {k: (base[k], pr[k]) for k in pr if k in base and base[k] != pr[k]}

    print(f"SBOM-diff: +{len(added)} -{len(removed)} ~{len(changed)}")
    for k in sorted(added):
        print(f"  + {k}=={added[k]}")
    for k in sorted(changed):
        print(f"  ~ {k}: {changed[k][0]} -> {changed[k][1]}")
    for k in sorted(removed):
        print(f"  - {k}=={removed[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
