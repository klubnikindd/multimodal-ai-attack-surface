#!/usr/bin/env python3
"""CI-гейт license-weights: манифест весов обязан быть fail-closed (§12.3)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.licensing import validate_manifest_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить лицензии весов моделей")
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = validate_manifest_file(args.manifest, target_profile=args.profile)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for model in report.models:
            print(
                f"{model['id']}: enabled={model['enabled']} license={model['license']} "
                f"profiles={model['allowed_profiles']}"
            )
        for warning in report.warnings:
            print(f"WARN: {warning}")
        for issue in report.issues:
            print(f"FAIL: {issue}")
        print(f"enabled={report.enabled_count} issues={len(report.issues)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
