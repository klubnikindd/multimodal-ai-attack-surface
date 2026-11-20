"""Безопасные пути и санитизация сегментов (SEC-06, T06)."""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Sequence
from pathlib import Path

from ..constants import MAX_PATH_LENGTH, MAX_PATH_SEGMENT
from ..errors import PathSafetyError

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_DOTS = re.compile(r"\.{2,}")
_RESERVED_WINDOWS = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def sanitize_segment(value: object, *, max_length: int = MAX_PATH_SEGMENT) -> str:
    """Allowlist-санитизация одного компонента пути.

    Возвращает безопасный сегмент; ``..``/``.``/абсолютные пути не проходят.
    """
    text = unicodedata.normalize("NFC", "" if value is None else str(value))
    text = text.replace("\u200b", "").replace("\ufeff", "")
    text = _FORBIDDEN.sub("_", text)
    text = _DOTS.sub("_", text)
    text = _SPACES.sub(" ", text).strip(" .")
    text = text.strip()
    if text in {"", ".", ".."}:
        return "_"
    if text.upper() in _RESERVED_WINDOWS or text.split(".", 1)[0].upper() in _RESERVED_WINDOWS:
        text = f"_{text}"
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    return text or "_"


def normalize_rel_path(rel_path: str | Path) -> str:
    """Нормализует относительный путь, сохраняя только безопасные компоненты."""
    raw = str(rel_path).replace("\\", "/")
    if "\x00" in raw:
        raise PathSafetyError("NUL в пути")
    if raw.startswith(("/", "\\")):
        raise PathSafetyError("Абсолютный путь запрещён")
    if _WINDOWS_DRIVE.match(raw):
        raise PathSafetyError("Windows drive-путь запрещён")
    if raw.startswith("//"):
        raise PathSafetyError("UNC-путь запрещён")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise PathSafetyError("Path traversal запрещён")
        if ":" in part:
            raise PathSafetyError("ADS/поток или drive-компонент запрещён")
        parts.append(sanitize_segment(part))
    if not parts:
        raise PathSafetyError("Пустой относительный путь")
    result = "/".join(parts)
    if len(result) > MAX_PATH_LENGTH:
        raise PathSafetyError("Слишком длинный путь")
    return result


def is_network_path(path: str | Path) -> bool:
    text = str(path)
    return text.startswith("//") or text.startswith("\\\\") or text.lower().startswith("smb://")


def safe_join(root: str | Path, rel_path: str | Path) -> Path:
    """Собирает путь внутри root и проверяет realpath-инвариант."""
    root_path = Path(root).expanduser().resolve()
    rel = normalize_rel_path(rel_path)
    candidate = (root_path / rel).resolve()
    try:
        common = Path(os.path.commonpath([str(root_path), str(candidate)]))
    except ValueError as exc:
        raise PathSafetyError("Путь вне library_root (разные тома)") from exc
    if common != root_path:
        raise PathSafetyError("Путь вне library_root")
    if candidate == root_path:
        raise PathSafetyError("Целевой путь совпадает с корнем")
    # Симлинк-компоненты запрещены: иначе realpath-проверка может быть обойдена.
    current = root_path
    for part in rel.split("/"):
        current = current / part
        if current.exists() and current.is_symlink():
            raise PathSafetyError(f"Симлинк в целевом пути: {part}")
    return candidate


def validate_destination(root: str | Path, destination: str | Path) -> Path:
    """Проверяет существующий/будущий dst перед каждой операцией."""
    root_path = Path(root).expanduser().resolve()
    dst = Path(destination)
    if dst.is_absolute():
        try:
            rel = dst.resolve().relative_to(root_path)
        except ValueError as exc:
            raise PathSafetyError("Абсолютный dst вне library_root") from exc
    else:
        rel = Path(normalize_rel_path(dst))
    result = safe_join(root_path, rel)
    parent = result.parent
    parent.mkdir(parents=True, exist_ok=True)
    try:
        result.resolve().relative_to(root_path)
    except ValueError as exc:
        raise PathSafetyError("dst вне library_root после resolve") from exc
    return result


def ensure_not_protected(path: str | Path, protected_roots: list[str | Path]) -> None:
    target = Path(path).resolve()
    for protected in protected_roots:
        root = Path(protected).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        raise PathSafetyError(f"Операция внутри защищённого каталога: {root}")


_SYSTEM_ROOTS = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "C:\\Windows",
    "C:\\Program Files",
)


def validate_scan_root(
    root: str | Path,
    *,
    allowed_roots: Sequence[str | Path] | None = None,
    forbidden_roots: Sequence[str | Path] | None = None,
) -> Path:
    """Ограничивает API/CLI-скан утверждёнными корнями (SEC-10)."""
    resolved = Path(root).expanduser().resolve()
    if not resolved.is_dir():
        raise PathSafetyError(f"Каталог не найден: {resolved}")
    if allowed_roots:
        allowed = [Path(item).expanduser().resolve() for item in allowed_roots]
        if not any(resolved == item or item in resolved.parents for item in allowed):
            raise PathSafetyError("Сканирование вне разрешённых KADR_SCAN_ROOTS запрещено")
    deny = [Path(item).resolve() for item in (forbidden_roots or [])]
    for system_root in _SYSTEM_ROOTS:
        candidate = Path(system_root)
        if candidate.exists() or os.name == "nt":
            deny.append(candidate.resolve())
    home = Path.home().resolve()
    if resolved == home:
        raise PathSafetyError("Сканирование всего домашнего каталога запрещено; выберите подпапку")
    for denied in deny:
        if resolved == denied or denied in resolved.parents:
            raise PathSafetyError(f"Системный каталог запрещён для скана: {denied}")
    return resolved


def is_cloud_placeholder(path: str | Path) -> bool:
    """Эвристика облачных заглушек без гидратации содержимого."""
    p = Path(path)
    try:
        stat = p.stat()
    except OSError:
        return False
    # Windows FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS / OFFLINE
    attrs = getattr(stat, "st_file_attributes", 0)
    if attrs & 0x00400000 or attrs & 0x00001000:
        return True
    name = p.name.lower()
    return name.endswith((".cloud", ".lnk")) and ".onedrive" in str(p).lower()
