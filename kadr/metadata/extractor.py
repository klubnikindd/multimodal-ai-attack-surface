"""Извлечение L0-метаданных без доверия к входному файлу (SEC-05/SEC-09)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..constants import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    KIND_BY_EXTENSION,
    RAW_EXTENSIONS,
    VIDEO_EXTENSIONS,
)
from ..errors import MetadataError
from ..models import MediaMetadata
from .dates import resolve_taken_at
from .sanitize import detect_injection, detect_pii, sanitize_text

try:  # pragma: no cover - зависит от окружения
    from PIL import Image
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]

try:  # pragma: no cover
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover
    MutagenFile = None


_EXIF_KEYS = {
    "make": 271,
    "model": 272,
    "orientation": 274,
    "software": 305,
    "datetime": 306,
    "artist": 315,
    "copyright": 33432,
    "description": 270,
    "datetime_original": 36867,
    "datetime_digitized": 36868,
    "offset_original": 36880,
    "offset_digitized": 36881,
    "offset": 36882,
    "exposure_time": 33434,
    "f_number": 33437,
    "iso": 34855,
    "focal_length": 37386,
    "lens": 42036,
    "title": 40091,
    "xp_keywords": 40094,
}

_SCREENSHOT_NAMES = ("screenshot", "screen shot", "скрин", "screencapture", "capture d'écran")


def _rational(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            return float(value.numerator) / float(value.denominator)
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return float(value[0]) / float(value[1])
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _gps_dms(values: Any) -> float | None:
    try:
        parts = list(values)
        if len(parts) != 3:
            return None
        degrees = _rational(parts[0])
        minutes = _rational(parts[1])
        seconds = _rational(parts[2])
        if degrees is None or minutes is None or seconds is None:
            return None
        return degrees + minutes / 60.0 + seconds / 3600.0
    except (TypeError, ValueError):
        return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return None
    text = sanitize_text(value)
    return text or None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _image_metadata(path: Path, result: MediaMetadata) -> None:
    if Image is None:
        result.parse_errors.append("Pillow не установлен: размеры/EXIF недоступны")
        return
    try:
        with Image.open(path) as img:
            result.width, result.height = img.size
            result.raw_json["format"] = img.format
            result.raw_json["mode"] = img.mode
            info = img.info or {}
            combined_text: list[str] = []
            for key in ("Description", "Comment", "Software", "Artist", "Copyright", "Title"):
                value = _string(info.get(key))
                if value:
                    combined_text.append(value)
                    result.raw_json.setdefault("info", {})[key] = value
                    if key in {"Description", "Comment"} and not result.description:
                        result.description = value
                    if key == "Title" and not result.title:
                        result.title = value
            if "XML:com.adobe.xmp" in info:
                result.raw_json["embedded_xmp_present"] = True

            exif = img.getexif()
            exif_values: dict[str, Any] = {}
            if exif:
                for name, tag in _EXIF_KEYS.items():
                    value = exif.get(tag)
                    if value is None:
                        continue
                    if name in {"exposure_time", "f_number", "focal_length"}:
                        numeric = _rational(value)
                        if numeric is not None:
                            exif_values[name] = numeric
                    elif name == "xp_keywords":
                        text = _string(value)
                        if text:
                            exif_values[name] = text
                    else:
                        text = _string(value)
                        if text is not None:
                            exif_values[name] = text
                try:
                    exif_ifd = exif.get_ifd(0x8769)
                except Exception:
                    exif_ifd = {}
                for name, tag in _EXIF_KEYS.items():
                    if tag in exif_ifd and name not in exif_values:
                        value = exif_ifd.get(tag)
                        if name in {"exposure_time", "f_number", "focal_length"}:
                            numeric = _rational(value)
                            if numeric is not None:
                                exif_values[name] = numeric
                        else:
                            text = _string(value)
                            if text is not None:
                                exif_values[name] = text
                try:
                    gps_ifd = exif.get_ifd(0x8825)
                except Exception:
                    gps_ifd = {}
                if gps_ifd:
                    lat = _gps_dms(gps_ifd.get(2))
                    lon = _gps_dms(gps_ifd.get(4))
                    if lat is not None:
                        if str(gps_ifd.get(1, "N")).upper() == "S":
                            lat = -lat
                        result.gps_lat = round(lat, 7)
                    if lon is not None:
                        if str(gps_ifd.get(3, "E")).upper() == "W":
                            lon = -lon
                        result.gps_lon = round(lon, 7)
            if exif_values:
                result.raw_json["exif"] = exif_values
                result.camera_make = exif_values.get("make") or result.camera_make
                result.camera_model = exif_values.get("model") or result.camera_model
                result.lens = exif_values.get("lens") or result.lens
                result.iso = _int_or_none(exif_values.get("iso")) or result.iso
                result.exposure_time = str(exif_values.get("exposure_time")) if exif_values.get("exposure_time") else None
                result.f_number = float(exif_values["f_number"]) if exif_values.get("f_number") else None
                result.focal_length = (
                    float(exif_values["focal_length"]) if exif_values.get("focal_length") else None
                )
                result.orientation = _int_or_none(exif_values.get("orientation"))
                result.description = exif_values.get("description") or result.description
                result.title = exif_values.get("title") or result.title
                result.raw_json["text_fields"] = combined_text
    except Image.DecompressionBombError:
        # SEC-09/T09: decompression-бомба обязана отклонить файл целиком,
        # а не превращаться в «частичные метаданные».
        raise
    except Exception as exc:  # битые/обрезанные файлы не роняют процесс
        result.parse_errors.append(f"image parse: {type(exc).__name__}: {exc}")


def _ffprobe(path: Path) -> dict[str, Any] | None:
    executable = shutil.which("ffprobe")
    if not executable:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - argv без shell
            [
                executable,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                "--",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode != 0 or not completed.stdout:
            return None
        return json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _av_metadata(path: Path, result: MediaMetadata) -> None:
    info = _ffprobe(path)
    if info is not None:
        result.raw_json["ffprobe"] = info
        fmt = info.get("format", {}) or {}
        duration = fmt.get("duration")
        try:
            if duration is not None:
                result.duration_ms = int(float(duration) * 1000)
        except (TypeError, ValueError):
            pass
        tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
        result.tags.extend(_split_tags(tags.get("keywords")))
        result.description = _string(tags.get("description") or tags.get("comment")) or result.description
        result.title = _string(tags.get("title")) or result.title
        result.raw_json["quicktime_creation_date"] = tags.get("creation_time")
        streams = info.get("streams") or []
        for stream in streams:
            if stream.get("codec_type") == "video" and result.width is None:
                result.width = stream.get("width")
                result.height = stream.get("height")
                result.raw_json["codec"] = stream.get("codec_name")
                result.raw_json["fps"] = stream.get("avg_frame_rate")
                result.raw_json["bitrate"] = stream.get("bit_rate")
                break
        if streams and result.raw_json.get("codec") is None:
            preferred_type = "video" if result.kind == "video" else "audio" if result.kind == "audio" else None
            first = next(
                (stream for stream in streams if preferred_type and stream.get("codec_type") == preferred_type),
                streams[0],
            )
            result.raw_json["codec"] = first.get("codec_name")
            result.raw_json["bitrate"] = first.get("bit_rate")
            result.raw_json["codec_stream_type"] = first.get("codec_type")
    if MutagenFile is not None:
        try:
            audio = MutagenFile(path)
            if audio is not None and audio.tags:
                result.raw_json["id3"] = {
                    str(key): str(value)[:512]
                    for key, value in list(audio.tags.items())[:64]
                }
                for key in ("TDRC", "date", "year"):
                    if key in audio.tags:
                        result.raw_json["id3_tdrc"] = str(audio.tags[key])[:64]
                        break
                for key in ("TIT2", "title"):
                    if key in audio.tags:
                        result.title = _string(str(audio.tags[key])) or result.title
                        break
                for key in ("TCON", "genre"):
                    if key in audio.tags:
                        result.tags.extend(_split_tags(str(audio.tags[key])))
                        break
        except Exception as exc:
            result.parse_errors.append(f"audio parse: {type(exc).__name__}: {exc}")


def _split_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = str(value).replace(";", ",").split(",")
    tags = []
    for part in parts:
        text = sanitize_text(part, limit=128)
        if text and text not in tags:
            tags.append(text)
    return tags[:100]


def _xmp_metadata(path: Path, result: MediaMetadata) -> None:
    try:
        from ..xmp import read_xmp_sidecar

        xmp = read_xmp_sidecar(path)
    except Exception as exc:  # pragma: no cover - защитный контур
        result.parse_errors.append(f"xmp parse: {type(exc).__name__}: {exc}")
        return
    if not xmp:
        return
    result.raw_json["xmp"] = xmp
    result.tags.extend(tag for tag in xmp.get("tags", []) if tag not in result.tags)
    result.description = xmp.get("description") or result.description
    result.title = xmp.get("title") or result.title
    if xmp.get("rating") is not None:
        try:
            result.rating = int(xmp["rating"])
        except (TypeError, ValueError):
            pass
    result.label = xmp.get("label") or result.label
    if xmp.get("date_created") and not result.raw_json.get("xmp_date_created"):
        result.raw_json["xmp_date_created"] = xmp["date_created"]


def _guess_media_class(path: Path, result: MediaMetadata, *, exif_present: bool) -> str:
    name = path.name.lower()
    if result.kind == "screenshot":
        return "screenshot"
    if any(marker in name for marker in _SCREENSHOT_NAMES):
        result.scene_type = "screenshot"
        return "screenshot"
    if result.kind == "document":
        return "document"
    if any(marker in name for marker in ("scan", "document", "receipt", "документ", "чек", "квитанция")):
        result.scene_type = "document"
        return "document"
    if result.kind in {"video", "audio", "raw"}:
        return result.kind
    if result.kind != "photo":
        return "unknown"
    width, height = result.width or 0, result.height or 0
    common_screens = {
        (1920, 1080),
        (1080, 1920),
        (2560, 1440),
        (1440, 2560),
        (1366, 768),
        (3840, 2160),
        (2160, 3840),
        (1170, 2532),
        (2532, 1170),
        (1284, 2778),
        (2778, 1284),
    }
    if not exif_present and path.suffix.lower() in {".png", ".gif", ".webp"}:
        if (width, height) in common_screens:
            result.scene_type = "screenshot"
            return "screenshot"
        return "graphic"
    return "photo"


def extract_metadata(path: str | Path, *, with_xmp: bool = True) -> MediaMetadata:
    file_path = Path(path)
    if not file_path.is_file():
        raise MetadataError(f"Файл не найден: {file_path}")
    ext = file_path.suffix.lower()
    kind = KIND_BY_EXTENSION.get(ext, "other")
    result = MediaMetadata(kind=kind)
    stat = file_path.stat()
    result.raw_json["file"] = {
        "name": file_path.name,
        "ext": ext,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
    }

    if ext in IMAGE_EXTENSIONS or ext in RAW_EXTENSIONS:
        if ext in RAW_EXTENSIONS:
            result.raw_json["raw"] = True
        try:
            _image_metadata(file_path, result)
        except Image.DecompressionBombError:
            # SEC-09/T09: decompression-бомба обязана отклонить файл, а не дать
            # «частичные метаданные» и пройти дальше молча.
            raise
        except Exception as exc:  # pragma: no cover
            result.parse_errors.append(f"image: {type(exc).__name__}: {exc}")
    if ext in VIDEO_EXTENSIONS or ext in AUDIO_EXTENSIONS:
        _av_metadata(file_path, result)
    if with_xmp:
        _xmp_metadata(file_path, result)

    exif_present = bool(result.raw_json.get("exif"))
    result.media_class = _guess_media_class(file_path, result, exif_present=exif_present)
    if result.scene_type is None and result.media_class == "document":
        result.scene_type = "document"

    resolution = resolve_taken_at(
        exif_datetime_original=result.raw_json.get("exif", {}).get("datetime_original"),
        exif_offset_original=result.raw_json.get("exif", {}).get("offset_original"),
        exif_create_date=result.raw_json.get("exif", {}).get("datetime_digitized")
        or result.raw_json.get("exif", {}).get("datetime"),
        exif_offset_create=result.raw_json.get("exif", {}).get("offset_digitized")
        or result.raw_json.get("exif", {}).get("offset"),
        xmp_date_created=result.raw_json.get("xmp_date_created"),
        quicktime_creation_date=result.raw_json.get("quicktime_creation_date"),
        id3_tdrc=result.raw_json.get("id3_tdrc"),
        filename=file_path.name,
        mtime=stat.st_mtime,
    )
    result.taken_at = resolution.taken_at
    result.taken_at_source = resolution.source
    result.date_confidence = resolution.confidence
    result.tz_offset = resolution.tz_offset

    text_fields = list(result.raw_json.get("text_fields") or [])
    for value in (
        result.description,
        result.title,
        result.raw_json.get("exif", {}).get("artist"),
        result.raw_json.get("exif", {}).get("copyright"),
        result.raw_json.get("exif", {}).get("software"),
    ):
        if value:
            text_fields.append(str(value))
    text_fields.extend(result.tags)
    injection = False
    pii = False
    injection_hits: list[str] = []
    for text in text_fields:
        hit, matches = detect_injection(text)
        injection = injection or hit
        injection_hits.extend(matches)
        pii = pii or bool(detect_pii(text))
    result.injection_suspected = injection
    result.pii_suspected = pii
    if injection:
        result.raw_json["injection_hits"] = injection_hits[:20]
    return result


def extract_metadata_json(path: str | Path) -> dict[str, Any]:
    meta = extract_metadata(path)
    payload = {
        "kind": meta.kind,
        "media_class": meta.media_class,
        "scene_type": meta.scene_type,
        "width": meta.width,
        "height": meta.height,
        "duration_ms": meta.duration_ms,
        "taken_at": meta.taken_at.isoformat() if meta.taken_at else None,
        "taken_at_source": meta.taken_at_source,
        "date_confidence": meta.date_confidence,
        "camera": {"make": meta.camera_make, "model": meta.camera_model, "lens": meta.lens},
        "gps": {"lat": meta.gps_lat, "lon": meta.gps_lon},
        "tags": meta.tags,
        "description": meta.description,
        "title": meta.title,
        "rating": meta.rating,
        "injection_suspected": meta.injection_suspected,
        "pii_suspected": meta.pii_suspected,
        "parse_errors": meta.parse_errors,
    }
    return payload


__all__ = ["extract_metadata", "extract_metadata_json"]
