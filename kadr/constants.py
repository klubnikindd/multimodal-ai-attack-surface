"""Константы домена: форматы, профили, закрытые словари, лимиты."""

from __future__ import annotations

import re

APP_NAME = "kadr"
CATALOG_VERSION = 3

# §5.1 FR-1.2
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".webp",
    ".avif",
    ".gif",
}
RAW_EXTENSIONS = {".cr2", ".cr3", ".nef", ".arw", ".dng", ".raf", ".orf", ".rw2"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
SIDECAR_EXTENSIONS = {".xmp", ".aae", ".dop", ".pp3", ".lrv", ".thm", ".srt", ".vtt", ".json"}

SUPPORTED_EXTENSIONS = (
    IMAGE_EXTENSIONS | RAW_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | SIDECAR_EXTENSIONS
)

RAW_NEVER_WRITE = frozenset(RAW_EXTENSIONS | {".braw", ".r3d"})
PROXY_EXTENSIONS = {".lrv", ".thm"}
EDIT_SIDECAR_EXTENSIONS = {".aae", ".dop", ".pp3"}

KIND_BY_EXTENSION: dict[str, str] = {}
for _ext in IMAGE_EXTENSIONS:
    KIND_BY_EXTENSION[_ext] = "photo"
for _ext in RAW_EXTENSIONS:
    KIND_BY_EXTENSION[_ext] = "raw"
for _ext in VIDEO_EXTENSIONS:
    KIND_BY_EXTENSION[_ext] = "video"
for _ext in AUDIO_EXTENSIONS:
    KIND_BY_EXTENSION[_ext] = "audio"
for _ext in SIDECAR_EXTENSIONS:
    KIND_BY_EXTENSION[_ext] = "sidecar"

# §8.2 профили качества
PROFILES = ("lite-forced", "lite", "balanced", "pro", "pro-plus")
VLM_PROFILES = frozenset({"balanced", "pro", "pro-plus"})

# OCR: язык tesseract задаётся настройкой KADR_OCR_LANG. Русскоязычный архив —
# целевой сценарий, поэтому по умолчанию просим rus+eng; отсутствие нужного
# traineddata видно как ошибка OCR, а не тихая подмена языка.
DEFAULT_OCR_LANGUAGE = "rus+eng"
OCR_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z0-9_+]{1,64}$")

# §8.3 ожидания для планирования (не обещание пользователю; источник фактов — BENCHMARKS)
DEFAULT_VLM_SECONDS_PER_FRAME = 18.0
VLM_CPU_SLOW_THRESHOLD_SECONDS = 25.0
VLM_WORKING_SET_GB = 4.0
VLM_DISK_GB = 3.0

# §8.4 закрытые словари контракта VLM
VLM_SCENE_TYPES = frozenset(
    {
        "wedding_ceremony",
        "portrait",
        "landscape",
        "street",
        "document",
        "receipt",
        "whiteboard",
        "screenshot",
        "food",
        "product",
        "architecture",
        "interior",
        "event",
        "sport",
        "concert",
        "other",
        "unknown",
    }
)
VLM_EVENT_TYPES = frozenset(
    {"wedding", "concert", "sport", "conference", "travel", "family", "product", "none", "other"}
)
VLM_SETTINGS = frozenset(
    {
        "outdoor_garden",
        "outdoor_city",
        "outdoor_nature",
        "indoor_home",
        "indoor_studio",
        "indoor_venue",
        "unknown",
    }
)
VLM_TIME_OF_DAY = frozenset(
    {"dawn", "morning", "day", "golden_hour", "sunset", "evening", "night", "unknown"}
)
VLM_WEATHER = frozenset({"clear", "cloudy", "rain", "snow", "fog", "unknown"})
VLM_LIGHTING = frozenset(
    {"natural", "natural_backlight", "artificial", "mixed", "low_light", "unknown"}
)
VLM_ALLOWED_FIELDS = frozenset(
    {
        "scene_type",
        "setting",
        "subjects",
        "actions",
        "event_type",
        "time_of_day",
        "weather",
        "lighting",
        "text_present",
        "quality_issues",
        "keywords_en",
        "caption_ru",
        "caption_en",
        "alt_text",
        "confidence",
    }
)
VLM_FORBIDDEN_FIELDS = frozenset(
    {"thinking", "reasoning", "chain_of_thought", "analysis", "tool_calls", "tools", "system"}
)
VLM_MAX_OUTPUT_TOKENS = 384
VLM_IMAGE_MAX_SIDE = 1024
VLM_NIGHT_IMAGE_MAX_SIDE = 768
VLM_PROMPT_REV = "vlm-json-v3-2026-11-13"
VLM_MAX_CAPTION_CHARS = 512
VLM_MAX_KEYWORDS = 50

# §11.2 lifecycle
SHUTDOWN_GRACE_SECONDS = 2.0
SHUTDOWN_KILL_SECONDS = 3.0
WATCHDOG_STALE_SECONDS = 5.0

# §12 лицензии
DENIED_WEIGHT_LICENSES = (
    "agpl-3.0",
    "agpl-3.0-only",
    "agpl-3.0-or-later",
    "cc-by-nc",
    "cc-by-nc-4.0",
    "cc-by-nc-sa",
    "research-only",
    "non-commercial",
    "gpl-2.0",
    "gpl-3.0",
)
UNKNOWN_LICENSE_VALUES = {"", "unknown", "none", "unverified", "tbd"}

# §5.2.2 токены шаблона пути
PATH_TOKENS = frozenset(
    {
        "date",
        "date.source",
        "camera.make",
        "camera.model",
        "lens",
        "iso",
        "kind",
        "ext",
        "ai.scene_type",
        "ai.event_type",
        "ai.doc_kind",
        "person.primary",
        "geo.country",
        "geo.city",
        "dup.group",
        "file.stem",
        "file.mtime",
        "hash",
        "counter",
    }
)
FREE_TEXT_PATH_TOKENS = frozenset({"ai.caption_short", "ai.caption", "ai.caption_ru"})

# §5.2.1 операторы
RULE_OPERATORS = frozenset(
    {"eq", "in", "gte", "lte", "between", "contains", "regex", "exists", "any", "all", "not"}
)
LOGICAL_KEYS = frozenset({"all", "any", "not"})

COLLISION_MODES = frozenset({"suffix", "skip", "ask", "hash"})
FILE_MODES = frozenset({"copy", "move", "hardlink"})

# §5.4 XMP
XMP_NAMESPACE_KADR = "https://kadr.app/ns/1.0/"
XMP_NAMESPACE_DC = "http://purl.org/dc/elements/1.1/"
XMP_NAMESPACE_XMP = "http://ns.adobe.com/xap/1.0/"
XMP_NAMESPACE_LR = "http://ns.adobe.com/lightroom/1.0/"
PRIVACY_PRESETS = ("stock", "social", "archive")

# Безопасные лимиты разбора (T09)
MAX_METADATA_TEXT = 4096
MAX_XMP_BYTES = 2 * 1024 * 1024
MAX_FILE_HASH_BYTES = 64 * 1024 * 1024 * 1024  # 64 ГиБ: не хэшируем бесконечные потоки
HASH_CHUNK = 1024 * 1024
MAX_PATH_SEGMENT = 64
MAX_PATH_LENGTH = 240

# Даты
DATE_SOURCE_CONFIDENCE = {
    "DateTimeOriginal": 1.0,
    "CreateDate": 0.95,
    "XMP DateCreated": 0.9,
    "QuickTime CreationDate": 0.85,
    "ID3 TDRC": 0.8,
    "filename": 0.6,
    "mtime": 0.3,
}

FILENAME_DATE_PATTERNS = (
    re.compile(r"(?P<y>19\d{2}|20\d{2})[-_]?(?P<m>0[1-9]|1[0-2])[-_]?(?P<d>0[1-9]|[12]\d|3[01])"),
    re.compile(r"(?P<y>19\d{2}|20\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])"),
    re.compile(
        r"(?P<y>19\d{2}|20\d{2})[-_](?P<m>0[1-9]|1[0-2])[-_](?P<d>0[1-9]|[12]\d|3[01])"
        r"[T _-](?P<H>[01]\d|2[0-3])(?P<M>[0-5]\d)(?P<S>[0-5]\d)?"
    ),
)
