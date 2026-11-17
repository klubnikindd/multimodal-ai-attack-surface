"""Иерархия источников даты (FR-1.4) и date_confidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..constants import DATE_SOURCE_CONFIDENCE, FILENAME_DATE_PATTERNS


@dataclass(slots=True)
class DateResolution:
    taken_at: datetime | None
    source: str | None
    confidence: float
    tz_offset: str | None = None


def _parse_offset(value: str | None) -> timezone | None:
    if not value:
        return None
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", value.strip())
    if not match:
        return None
    sign = 1 if match.group(1) == "+" else -1
    hours = int(match.group(2))
    minutes = int(match.group(3))
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_exif_datetime(value: str | None, offset: str | None = None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().strip("\x00")
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            tz = _parse_offset(offset)
            return parsed.replace(tzinfo=tz) if tz else parsed
        except ValueError:
            continue
    return None


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        # QuickTime иногда отдаёт "2025-07-12T13:22:00.000000Z"
        try:
            return datetime.fromisoformat(text[:26].replace("Z", "+00:00"))
        except ValueError:
            return None


def from_filename(filename: str | None) -> datetime | None:
    if not filename:
        return None
    stem = filename.rsplit(".", 1)[0]
    for pattern in FILENAME_DATE_PATTERNS:
        match = pattern.search(stem)
        if not match:
            continue
        try:
            year = int(match.group("y"))
            month = int(match.group("m"))
            day = int(match.group("d"))
            hour = int(match.groupdict().get("H") or 0)
            minute = int(match.groupdict().get("M") or 0)
            second = int(match.groupdict().get("S") or 0)
            return datetime(year, month, day, hour, minute, second)
        except (ValueError, TypeError):
            continue
    return None


def from_mtime(mtime: float | None) -> datetime | None:
    if mtime is None:
        return None
    try:
        return datetime.fromtimestamp(float(mtime), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def resolve_taken_at(
    *,
    exif_datetime_original: str | None = None,
    exif_offset_original: str | None = None,
    exif_create_date: str | None = None,
    exif_offset_create: str | None = None,
    xmp_date_created: str | None = None,
    quicktime_creation_date: str | None = None,
    id3_tdrc: str | None = None,
    filename: str | None = None,
    mtime: float | None = None,
) -> DateResolution:
    """Строгий порядок §5.1 FR-1.4. Возвращает источник и уверенность."""
    candidates: list[tuple[datetime | None, str, str | None]] = [
        (
            parse_exif_datetime(exif_datetime_original, exif_offset_original),
            "DateTimeOriginal",
            exif_offset_original,
        ),
        (parse_exif_datetime(exif_create_date, exif_offset_create), "CreateDate", exif_offset_create),
        (parse_iso_datetime(xmp_date_created), "XMP DateCreated", None),
        (parse_iso_datetime(quicktime_creation_date), "QuickTime CreationDate", None),
        (parse_iso_datetime(id3_tdrc), "ID3 TDRC", None),
        (from_filename(filename), "filename", None),
        (from_mtime(mtime), "mtime", None),
    ]
    for value, source, offset in candidates:
        if value is not None:
            return DateResolution(
                taken_at=value,
                source=source,
                confidence=DATE_SOURCE_CONFIDENCE.get(source, 0.5),
                tz_offset=offset,
            )
    return DateResolution(taken_at=None, source=None, confidence=0.0)
