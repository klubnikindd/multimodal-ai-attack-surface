"""XMP-sidecar: чтение, merge-запись и пресеты приватности (FR-4.x).

Политика продукта: по умолчанию пишем sidecar, оригиналы не изменяем,
внутрь RAW не пишем никогда. Embedded-запись — явный opt-in и только для
поддерживаемых контейнеров.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import (
    MAX_XMP_BYTES,
    PRIVACY_PRESETS,
    RAW_NEVER_WRITE,
    XMP_NAMESPACE_DC,
    XMP_NAMESPACE_KADR,
    XMP_NAMESPACE_LR,
    XMP_NAMESPACE_XMP,
)
from .errors import PathSafetyError
from .metadata.sanitize import detect_pii, redact_pii, sanitize_text

NS = {
    "dc": XMP_NAMESPACE_DC,
    "xmp": XMP_NAMESPACE_XMP,
    "lr": XMP_NAMESPACE_LR,
    "kadr": XMP_NAMESPACE_KADR,
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "x": "adobe:ns:meta/",
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)

RDF = f"{{{NS['rdf']}}}"
DC = f"{{{NS['dc']}}}"
XMP = f"{{{NS['xmp']}}}"
LR = f"{{{NS['lr']}}}"
KADR = f"{{{NS['kadr']}}}"
X = f"{{{NS['x']}}}"


def sidecar_path(file_path: str | Path) -> Path:
    path = Path(file_path)
    return path.with_suffix(".xmp")


def _read_limited(path: Path) -> bytes:
    if path.stat().st_size > MAX_XMP_BYTES:
        raise ValueError(f"XMP больше лимита {MAX_XMP_BYTES} байт")
    return path.read_bytes()


def _text_list(parent: ET.Element) -> list[str]:
    values: list[str] = []
    for li in parent.iter(f"{RDF}li"):
        text = sanitize_text(li.text or "", limit=512)
        if text and text not in values:
            values.append(text)
    return values


def read_xmp_sidecar(file_path: str | Path) -> dict[str, Any] | None:
    """Читает sidecar рядом с файлом. Битый XMP не роняет импорт."""
    path = sidecar_path(file_path)
    if not path.is_file():
        return None
    try:
        root = ET.fromstring(_read_limited(path))
    except (OSError, ValueError, ET.ParseError):
        return None
    result: dict[str, Any] = {"sidecar": str(path)}
    tags: list[str] = []
    for subject in root.iter(f"{DC}subject"):
        tags.extend(_text_list(subject))
    for hierarchical in root.iter(f"{LR}hierarchicalSubject"):
        for value in _text_list(hierarchical):
            if value not in tags:
                tags.append(value)
    if tags:
        result["tags"] = tags
    for description in root.iter(f"{DC}description"):
        values = _text_list(description)
        if values:
            result["description"] = values[0]
            break
    for title in root.iter(f"{DC}title"):
        values = _text_list(title)
        if values:
            result["title"] = values[0]
            break
    for date_created in root.iter(f"{XMP}CreateDate"):
        if date_created.text:
            result["date_created"] = date_created.text.strip()
            break
    for rating in root.iter(f"{XMP}Rating"):
        if rating.text:
            try:
                result["rating"] = int(rating.text.strip())
            except ValueError:
                pass
            break
    for label in root.iter(f"{XMP}Label"):
        if label.text:
            result["label"] = sanitize_text(label.text, limit=64)
            break
    provenance: dict[str, str] = {}
    for element in root.iter():
        tag = element.tag
        if not tag.startswith(KADR):
            continue
        key = tag[len(KADR) :]
        if element.text:
            provenance[key] = element.text.strip()
    if provenance:
        result["provenance"] = provenance
    return result


@dataclass(slots=True)
class XmpWriteResult:
    path: str
    written: bool
    conflicts: list[str] = field(default_factory=list)
    skipped_reason: str | None = None
    tags_written: list[str] = field(default_factory=list)
    backup: str | None = None


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:  # pragma: no cover - Windows
        pass


def _find_or_create_description(root: ET.Element) -> ET.Element:
    for desc in root.iter(f"{RDF}Description"):
        if desc.get(f"{RDF}about", "") in {"", None}:
            return desc
    rdf = root.find(f"{RDF}RDF")
    if rdf is None:
        rdf = ET.SubElement(root, f"{RDF}RDF")
    return ET.SubElement(rdf, f"{RDF}Description")


def _set_bag(description: ET.Element, qualified: str, values: list[str]) -> None:
    existing = description.find(qualified)
    if existing is None:
        existing = ET.SubElement(description, qualified)
        bag = ET.SubElement(existing, f"{RDF}Bag")
    else:
        found = existing.find(f"{RDF}Bag")
        bag = found if found is not None else ET.SubElement(existing, f"{RDF}Bag")
    current = _text_list(bag)
    for value in values:
        if value and value not in current:
            li = ET.SubElement(bag, f"{RDF}li")
            li.text = value
            current.append(value)


def _set_alt(description: ET.Element, qualified: str, value: str, lang: str = "x-default") -> None:
    existing = description.find(qualified)
    if existing is None:
        existing = ET.SubElement(description, qualified)
    alt = existing.find(f"{RDF}Alt")
    if alt is None:
        alt = ET.SubElement(existing, f"{RDF}Alt")
    for li in alt.findall(f"{RDF}li"):
        if li.get("{http://www.w3.org/XML/1998/namespace}lang") == lang:
            return  # merge, а не replace
    li = ET.SubElement(alt, f"{RDF}li")
    li.set("{http://www.w3.org/XML/1998/namespace}lang", lang)
    li.text = value


def _set_simple(
    description: ET.Element, qualified: str, value: str, *, overwrite: bool = False
) -> None:
    """Merge-семантика: по умолчанию существующее пользовательское значение не трогаем."""
    existing = description.find(qualified)
    if existing is not None:
        if overwrite:
            existing.text = value
        return
    element = ET.SubElement(description, qualified)
    element.text = value


def _sanitize_tags(tags: list[str]) -> list[str]:
    result: list[str] = []
    for tag in tags:
        text = sanitize_text(tag, limit=128)
        if text and text not in result:
            result.append(text)
    return result[:100]


def _privacy_filter(payload: dict[str, Any], preset: str) -> dict[str, Any]:
    """Пресеты §5.4 FR-4.6. В sidecar по умолчанию нет GPS/серийников."""
    if preset not in PRIVACY_PRESETS:
        raise ValueError(f"Неизвестный пресет приватности: {preset}")
    data = dict(payload)
    tags = _sanitize_tags(list(data.get("tags") or []))
    if preset in {"stock", "social"}:
        # PII в тегах и описаниях не должно уходить в экспорт.
        tags = [tag for tag in tags if not detect_pii(tag)]
        for key in ("description", "title"):
            value = data.get(key)
            if value:
                data[key] = redact_pii(sanitize_text(value, limit=1024))
        data.pop("gps", None)
        data.pop("serial", None)
        data.pop("persons", None)
    if preset == "social":
        data.pop("author", None)
        data.pop("copyright", None)
    data["tags"] = tags
    return data


def write_xmp_sidecar(
    file_path: str | Path,
    *,
    tags: list[str] | None = None,
    description: str | None = None,
    title: str | None = None,
    rating: int | None = None,
    label: str | None = None,
    provenance: dict[str, Any] | None = None,
    preset: str = "archive",
) -> XmpWriteResult:
    """Merge-запись XMP sidecar с атомарной заменой. RAW не изменяется."""
    source = Path(file_path)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    payload = _privacy_filter(
        {
            "tags": list(tags or []),
            "description": description,
            "title": title,
            "rating": rating,
            "label": label,
            "provenance": provenance or {},
        },
        preset,
    )
    target = sidecar_path(source)
    conflicts: list[str] = []
    if target.is_file():
        try:
            root = ET.fromstring(_read_limited(target))
        except (OSError, ValueError, ET.ParseError):
            return XmpWriteResult(
                path=str(target),
                written=False,
                skipped_reason="existing XMP is invalid; not overwriting",
                conflicts=["invalid-existing-xmp"],
            )
    else:
        root = ET.Element(f"{X}xmpmeta")
        ET.SubElement(root, f"{RDF}RDF")
    description_el = _find_or_create_description(root)
    description_el.set(f"{RDF}about", "")
    _set_bag(description_el, f"{DC}subject", payload["tags"])
    if payload.get("description"):
        _set_alt(description_el, f"{DC}description", payload["description"])
    if payload.get("title"):
        _set_alt(description_el, f"{DC}title", payload["title"])
    if payload.get("rating") is not None:
        _set_simple(description_el, f"{XMP}Rating", str(int(payload["rating"])))
    if payload.get("label"):
        _set_simple(description_el, f"{XMP}Label", payload["label"])
    provenance = payload.get("provenance") or {}
    for key, value in provenance.items():
        safe_key = "".join(ch for ch in str(key) if ch.isalnum() or ch in "_-")[:64]
        if not safe_key:
            continue
        _set_simple(description_el, f"{KADR}{safe_key}", sanitize_text(value, limit=512))

    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".kadr-xmp-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
        _fsync_dir(target.parent)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return XmpWriteResult(
        path=str(target),
        written=True,
        conflicts=conflicts,
        tags_written=list(payload["tags"]),
    )


def write_embedded_metadata(
    file_path: str | Path,
    *,
    tags: list[str] | None = None,
    description: str | None = None,
    title: str | None = None,
    preset: str = "stock",
) -> XmpWriteResult:
    """Opt-in embedded-запись. RAW и неподдерживаемые контейнеры отклоняются.

    Оригинал не теряет EXIF/ICC: они переносятся в новый контейнер, запись
    атомарная, рядом остаётся ``*.kadr-backup`` (путь возвращается в результате).
    """
    source = Path(file_path)
    ext = source.suffix.lower()
    if ext in RAW_NEVER_WRITE:
        raise PathSafetyError("Запись внутрь RAW запрещена политикой продукта")
    payload = _privacy_filter(
        {"tags": list(tags or []), "description": description, "title": title}, preset
    )
    if ext not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
        return XmpWriteResult(
            path=str(source),
            written=False,
            skipped_reason=f"embedded write for {ext} is not enabled in v0.1",
        )
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return XmpWriteResult(
            path=str(source), written=False, skipped_reason="Pillow is not installed"
        )

    # Собираем минимальный XMP-пакет и передаём его Pillow через xmp=.
    root = ET.Element(f"{X}xmpmeta")
    rdf = ET.SubElement(root, f"{RDF}RDF")
    desc = ET.SubElement(rdf, f"{RDF}Description", {f"{RDF}about": ""})
    _set_bag(desc, f"{DC}subject", payload["tags"])
    if payload.get("description"):
        _set_alt(desc, f"{DC}description", payload["description"])
    if payload.get("title"):
        _set_alt(desc, f"{DC}title", payload["title"])
    xmp_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    backup = source.with_suffix(source.suffix + ".kadr-backup")
    if backup.exists():
        backup = source.with_suffix(source.suffix + f".kadr-backup-{os.getpid()}")
    shutil.copy2(source, backup)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".kadr-embed-", suffix=source.suffix, dir=str(source.parent)
    )
    os.close(fd)
    try:
        with Image.open(source) as img:
            # Сырые байты EXIF сохраняют IFD1/thumbnail без переупаковки;
            # getexif() читаем максимум один раз и только как fallback.
            exif_bytes = img.info.get("exif")
            if not exif_bytes and hasattr(img, "getexif"):
                exif_obj = img.getexif()
                exif_bytes = exif_obj.tobytes() if len(exif_obj) else None
            icc_profile = img.info.get("icc_profile")
            pnginfo = None
            if ext == ".png":
                try:
                    from PIL import PngImagePlugin

                    pnginfo = PngImagePlugin.PngInfo()
                    for key, value in (img.info or {}).items():
                        if isinstance(value, str) and key not in {"exif", "icc_profile"}:
                            pnginfo.add_text(key, value)
                except Exception:
                    pnginfo = None
            save_kwargs: dict[str, Any] = {"xmp": xmp_bytes}
            if exif_bytes:
                save_kwargs["exif"] = exif_bytes
            if icc_profile:
                save_kwargs["icc_profile"] = icc_profile
            if pnginfo is not None:
                save_kwargs["pnginfo"] = pnginfo
            if ext in {".jpg", ".jpeg"}:
                save_kwargs["quality"] = "keep"
                save_kwargs["subsampling"] = "keep"
            try:
                img.save(tmp_name, format=img.format, **save_kwargs)
            except (OSError, ValueError):
                # Часть версий Pillow не поддерживает quality="keep" вместе с XMP.
                if ext not in {".jpg", ".jpeg"}:
                    raise
                save_kwargs["quality"] = 95
                save_kwargs.pop("subsampling", None)
                img.save(tmp_name, format=img.format, **save_kwargs)
        # Проверяем, что metadata не потеряна до замены оригинала.
        with Image.open(source) as original_img:
            original_exif_count = len(original_img.getexif())
        with Image.open(tmp_name) as saved:
            if original_exif_count and not len(saved.getexif()):
                raise ValueError("embedded write would drop EXIF")
            if icc_profile and not saved.info.get("icc_profile"):
                raise ValueError("embedded write would drop ICC profile")
        # fsync требует дескриптор с правом записи (Windows: _commit на
        # read-only handle падает с EBADF). Данные при этом не меняются.
        with open(tmp_name, "rb+") as fh:
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.replace(tmp_name, source)
        except OSError as exc:
            raise RuntimeError(f"replace failed; backup at {backup}") from exc
        _fsync_dir(source.parent)
    except Exception:
        # Оригинал не тронут; бэкап остаётся для ручного восстановления.
        raise
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return XmpWriteResult(
        path=str(source),
        written=True,
        tags_written=list(payload["tags"]),
        backup=str(backup),
    )


def export_metadata(metadata: dict[str, Any], *, preset: str = "stock") -> dict[str, Any]:
    """Экспортный пресет: `stock` по умолчанию удаляет GPS/серийники/лица."""
    if preset not in PRIVACY_PRESETS:
        raise ValueError(f"Неизвестный пресет приватности: {preset}")
    data = dict(metadata)
    if preset in {"stock", "social"}:
        data.pop("gps", None)
        data.pop("gps_lat", None)
        data.pop("gps_lon", None)
        data.pop("serial", None)
        data.pop("camera_serial", None)
        data.pop("lens_serial", None)
        data.pop("faces", None)
        data.pop("persons", None)
        data["tags"] = [tag for tag in data.get("tags", []) if not detect_pii(str(tag))]
        if data.get("description"):
            data["description"] = redact_pii(str(data["description"]))
    if preset == "social":
        data.pop("author", None)
        data.pop("copyright", None)
        data.pop("description", None)
    return data


__all__ = [
    "XmpWriteResult",
    "export_metadata",
    "read_xmp_sidecar",
    "sidecar_path",
    "write_embedded_metadata",
    "write_xmp_sidecar",
]
