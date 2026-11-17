#!/usr/bin/env python3
"""Проверяет инварианты спецификаций в docs/spec/."""

import re
import sys
from pathlib import Path

SPEC_DIR = Path("docs/spec")
ERRORS = []

def check_current_count():
    """Ровно один файл со статусом current."""
    current = []
    for md in SPEC_DIR.glob("*.md"):
        text = md.read_text(encoding="utf-8")
        if re.search(r'status:\s*current', text, re.IGNORECASE):
            current.append(md.name)
    if len(current) != 1:
        ERRORS.append(f"Ожидался ровно один 'current', найдено: {current or 'ни одного'}")

def check_v1_archived():
    """v1.md должен быть archived."""
    v1 = SPEC_DIR / "v1.md"
    if not v1.exists():
        ERRORS.append("v1.md отсутствует")
        return
    text = v1.read_text(encoding="utf-8")
    if not re.search(r'status:\s*archived', text, re.IGNORECASE):
        ERRORS.append("v1.md должен иметь status: archived")

def check_no_uploads_refs():
    """Никаких ссылок на uploads/ в docs/."""
    for md in Path("docs").rglob("*.md"):
        text = md.read_text(encoding="utf-8")
        if "uploads/" in text:
            ERRORS.append(f"{md}: найдена ссылка на 'uploads/'")

def check_readme_lists_current():
    """README.md в docs/spec/ должен содержать имя current-файла."""
    readme = SPEC_DIR / "README.md"
    if not readme.exists():
        ERRORS.append("docs/spec/README.md отсутствует")
        return
    text = readme.read_text(encoding="utf-8")
    for md in SPEC_DIR.glob("v*.md"):
        if re.search(r'status:\s*current', md.read_text(encoding="utf-8"), re.IGNORECASE):
            if md.name not in text:
                ERRORS.append(f"README.md не упоминает current-файл {md.name}")

if __name__ == "__main__":
    if not SPEC_DIR.exists():
        sys.exit("docs/spec/ не существует")

    check_current_count()
    check_v1_archived()
    check_no_uploads_refs()
    check_readme_lists_current()

    if ERRORS:
        print("❌ Нарушены инварианты спецификаций:")
        for e in ERRORS:
            print(f"  - {e}")
        sys.exit(1)
    print("✅ Все инварианты спецификаций соблюдены")