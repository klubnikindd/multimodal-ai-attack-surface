#!/usr/bin/env python3
"""Проверяет инварианты спецификаций в docs/spec/ (v3.1 §20/§21, P0-07).

Проверки:
  1. ровно один файл со status: current среди v*.md;
  2. каждый не-current v*.md помечен status: archived и имеет superseded_by,
     указывающий на существующий файл;
  3. README.md в docs/spec/ упоминает текущий файл;
  4. v1.md — archived (историческое требование);
  5. в docs/ нет ссылок на uploads/;
  6. §-ссылки текущей спеки указывают на существующие разделы;
  7. якоря оглавления (#...) текущей спеки существуют в её заголовках;
  8. нет временных файлов сборки спеки.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SPEC_DIR = Path("docs/spec")
ERRORS: list[str] = []

# §-ссылки на чужие документы, которые в этой спеке не обязаны существовать:
#   §3.5  — docs/PROPOSAL.md §3.5 (краевые случаи ФС, FR-2.9)
#   §1.1  — docs/IMPLEMENTATION_PLAN.md §1.1 (оценка фаз)
EXTERNAL_SECTION_REFS = {"3.5", "1.1"}
TEMP_SPEC_PATTERNS = (".build.md", ".tmp.md", "~", ".swp", ".orig", ".rej")


def _configure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - экзотические консоли
            pass


def _status(text: str) -> str:
    match = re.search(r"^status:\s*(\w+)", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _superseded_by(text: str) -> str:
    match = re.search(r"^superseded_by:\s*(\S+)", text, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip().strip('"') if match else ""


def _spec_files() -> list[Path]:
    return sorted(SPEC_DIR.glob("v*.md"))


def check_current_count(files: list[Path]) -> Path | None:
    current = [path for path in files if _status(path.read_text(encoding="utf-8")) == "current"]
    if len(current) != 1:
        ERRORS.append(
            "Ожидался ровно один 'current', найдено: "
            + (", ".join(path.name for path in current) or "ни одного")
        )
        return None
    return current[0]


def check_archived_banners(files: list[Path], current: Path | None) -> None:
    names = {path.name for path in files}
    for path in files:
        if current is not None and path == current:
            continue
        text = path.read_text(encoding="utf-8")
        if _status(text) != "archived":
            ERRORS.append(f"{path.name}: ожидался status: archived")
        target = _superseded_by(text)
        if not target:
            ERRORS.append(f"{path.name}: нет superseded_by")
        elif target not in names:
            ERRORS.append(f"{path.name}: superseded_by указывает на несуществующий {target}")


def check_v1_archived(files: list[Path]) -> None:
    v1 = SPEC_DIR / "v1.md"
    if not v1.exists():
        ERRORS.append("v1.md отсутствует")
        return
    if _status(v1.read_text(encoding="utf-8")) != "archived":
        ERRORS.append("v1.md должен иметь status: archived")


def check_no_uploads_refs() -> None:
    for md in Path("docs").rglob("*.md"):
        if "uploads/" in md.read_text(encoding="utf-8", errors="replace"):
            ERRORS.append(f"{md}: найдена ссылка на 'uploads/'")


def check_readme_lists_current(current: Path | None) -> None:
    readme = SPEC_DIR / "README.md"
    if not readme.exists():
        ERRORS.append("docs/spec/README.md отсутствует")
        return
    if current is None:
        return
    if current.name not in readme.read_text(encoding="utf-8"):
        ERRORS.append(f"README.md не упоминает current-файл {current.name}")


def _slug(heading: str) -> str:
    """GitHub-подобный якорь: пробел → «-», пунктуация удаляется."""
    out = []
    for char in heading.strip().lower():
        if char.isspace():
            out.append("-")
        elif char.isalnum() or char in {"-", "_"}:
            out.append(char)
    return "".join(out)


def check_section_refs(current: Path) -> None:
    text = current.read_text(encoding="utf-8")
    headings = {
        match.group(1)
        for match in re.finditer(r"^#{2,4}\s+(\d+(?:\.\d+)*)\.?\s", text, re.MULTILINE)
    }
    missing = sorted(
        {
            ref
            for ref in re.findall(r"§\s?(\d+(?:\.\d+)*)", text)
            if ref not in headings and ref not in EXTERNAL_SECTION_REFS
        }
    )
    if missing:
        ERRORS.append(f"{current.name}: §-ссылки без раздела: {', '.join(missing)}")


def check_toc_anchors(current: Path) -> None:
    text = current.read_text(encoding="utf-8")
    anchors = {
        _slug(match.group(2).strip())
        for match in re.finditer(r"^(#{1,6})\s+(.*)$", text, re.MULTILINE)
    }
    broken = []
    for match in re.finditer(r"\[[^\]]+\]\(#([^)]+)\)", text):
        anchor = match.group(1)
        if anchor not in anchors:
            broken.append(anchor)
    if broken:
        ERRORS.append(f"{current.name}: битые якоря оглавления: {', '.join(sorted(set(broken)))}")


def check_temp_files(files: list[Path]) -> None:
    for path in SPEC_DIR.iterdir():
        if path.is_file() and any(pattern in path.name for pattern in TEMP_SPEC_PATTERNS):
            ERRORS.append(f"временный файл спеки: {path.name}")


def main() -> int:
    _configure_stdout()
    if not SPEC_DIR.exists():
        sys.exit("docs/spec/ не существует")

    files = _spec_files()
    current = check_current_count(files)
    check_archived_banners(files, current)
    check_v1_archived(files)
    check_no_uploads_refs()
    check_readme_lists_current(current)
    check_temp_files(files)
    if current is not None:
        check_section_refs(current)
        check_toc_anchors(current)

    if ERRORS:
        print("FAIL: инварианты спецификаций нарушены:")
        for error in ERRORS:
            print(f"  - {error}")
        return 1
    print(f"OK: инварианты спецификаций соблюдены (current: {current.name if current else '—'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
