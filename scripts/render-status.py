#!/usr/bin/env python3
"""STATUS.md из фактических артефактов: pytest (junit), гейты, версии (P0-08).

Ручной STATUS расходился с кодом (BLK «дрейф документации»), поэтому таблица
результатов генерируется. Текст вне маркеров kadr:generated:start/end не
трогается — его пишут люди.

Использование:
    python scripts/render-status.py                       # только гейты
    python scripts/render-status.py --junit pytest.xml     # + итоги тестов
    python scripts/render-status.py --output STATUS.md
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
START = "<!-- kadr:generated:start -->"
END = "<!-- kadr:generated:end -->"

# Обязательные гейты (§11.2.4, §12.3 v3.1). tests считается по junit.
GATES: tuple[tuple[str, str | None], ...] = (
    ("tests", None),
    ("sqlite-vec-smoke", "scripts/sqlite_vec_smoke.py"),
    ("sidecar-lifecycle", "scripts/check-sidecar-lifecycle.py"),
    ("license-weights", "scripts/check-model-license.py"),
    ("model-integrity", "scripts/check-model-integrity.py"),
    ("license-code", "scripts/check-code-licenses.py"),
    ("sandbox-parity", "scripts/check-sandbox-parity.py"),
    ("spec-invariants", "scripts/check-spec-invariants.py"),
    ("no-onefile", "scripts/check-no-onefile.py"),
)


def parse_junit(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:  # pragma: no cover - битый артефакт
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0) or 0)
    return totals


def _summarize(output: str) -> str:
    """Короткая строка для STATUS: для JSON-гейтов — ключевые поля."""
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return ""
    head = lines[0].strip()
    if head.startswith("{") or head.startswith("["):
        try:
            data = json.loads("\n".join(lines))
        except json.JSONDecodeError:
            return lines[-1][:160]
        if isinstance(data, dict):
            parts = []
            for key in ("ok", "available", "status", "enabled", "issues", "components", "checks"):
                if key in data:
                    value = data[key]
                    if isinstance(value, list):
                        value = len(value)
                    elif isinstance(value, dict) and "status" in value:
                        value = value["status"]
                    parts.append(f"{key}={value}")
            if not parts:
                parts = [
                    f"{key}={str(data[key])[:40]}" for key in list(data)[:3]
                ]
            if parts:
                return ", ".join(parts)[:160]
    return lines[-1][:160]


def run_gate(relative: str, *, timeout: float = 300.0) -> tuple[int, str]:
    path = REPO_ROOT / relative
    if not path.exists():
        return 127, "скрипт отсутствует"
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(  # noqa: S603 - фиксированный argv
            [sys.executable, str(path)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"таймаут {timeout:.0f} c"
    return proc.returncode, _summarize(proc.stdout or proc.stderr or "")


def spec_version() -> str:
    spec_dir = REPO_ROOT / "docs" / "spec"
    for md in sorted(spec_dir.glob("v*.md"), key=lambda p: p.name):
        text = md.read_text(encoding="utf-8", errors="replace")
        if "status: current" in text:
            return md.name
    return "не найден"


def git_head() -> str:
    try:
        proc = subprocess.run(  # noqa: S603 - фиксированный argv
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return (proc.stdout or "").strip() or "n/a"
    except (OSError, subprocess.SubprocessError):
        return "n/a"


def build_block(*, junit: Path | None, run_gates: bool) -> str:
    tests = parse_junit(junit) if junit else None
    rows: list[str] = []
    failures = 0
    for name, script in GATES:
        if name == "tests":
            if tests is None:
                rows.append("| tests | ⚪ не измерено | нет junit-артефакта |")
                continue
            failed = tests["failures"] + tests["errors"]
            failures += failed
            icon = "✅" if failed == 0 else "❌"
            rows.append(
                "| tests | {} | {} тестов: {} fail, {} skip |".format(
                    icon, tests["tests"], failed, tests["skipped"]
                )
            )
            continue
        if not run_gates:
            rows.append(f"| {name} | ⚪ не проверялся | --no-gates |")
            continue
        code, detail = run_gate(script or "")
        if code != 0:
            failures += 1
        icon = "✅" if code == 0 else "❌"
        rows.append(f"| {name} | {icon} exit={code} | {detail} |")
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    head = git_head()
    verdict = "✅ все проверенные гейты зелёные" if failures == 0 else f"❌ красных: {failures}"
    lines = [
        START,
        "",
        f"**Сгенерировано:** {stamp} · **HEAD:** {head} · **спека:** docs/spec/{spec_version()} · **итог:** {verdict}",
        "",
        "| Гейт | Статус | Детали |",
        "|---|---|---|",
        *rows,
        "",
        "Источник: scripts/render-status.py (P0-08). Таблица перезаписывается, текст ниже — руками.",
        "",
        END,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сгенерировать блок STATUS.md из гейтов")
    parser.add_argument("--junit", default=None, help="путь к pytest junit-xml")
    parser.add_argument("--output", default=str(REPO_ROOT / "STATUS.md"))
    parser.add_argument("--no-gates", action="store_true", help="не запускать гейты")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    block = build_block(
        junit=Path(args.junit) if args.junit else None,
        run_gates=not args.no_gates,
    )
    output = Path(args.output)
    text = output.read_text(encoding="utf-8") if output.exists() else "# STATUS\n"
    if START in text and END in text:
        head, _, rest = text.partition(START)
        _, _, tail = rest.partition(END)
        new_text = head + block + tail
    else:
        first_line_end = text.find("\n")
        new_text = text[: first_line_end + 1] + "\n" + block + "\n" + text[first_line_end + 1 :]
    output.write_text(new_text, encoding="utf-8")
    if args.json:
        print(json.dumps({"output": str(output), "written": True}, ensure_ascii=False))
    else:
        print(f"STATUS обновлён: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
