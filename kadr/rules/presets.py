"""Готовые пресеты раскладки (FR-2.11)."""

from __future__ import annotations

import copy
from typing import Any

from ..errors import RuleError

_SAFETY = {
    "mode": "copy",
    "verify": "blake3",
    "never_delete": True,
    "keep_sidecars": True,
    "collision": "suffix",
}
_FALLBACK = {"path": "_Unsorted/{file.mtime:%Y-%m}", "review": True}

PRESETS: dict[str, dict[str, Any]] = {
    "date": {
        "version": 1,
        "safety": dict(_SAFETY),
        "rules": [
            {
                "id": "screenshots",
                "stop": True,
                "when": {"ai.scene_type": {"in": ["screenshot"]}},
                "then": {"path": "Screenshots/{date:%Y/%Y-%m}", "tags": ["скриншот"]},
            },
            {
                "id": "videos",
                "stop": True,
                "when": {"kind": {"eq": "video"}},
                "then": {"path": "Video/{date:%Y/%Y-%m-%d}", "tags": ["видео"]},
            },
            {
                "id": "audio",
                "stop": True,
                "when": {"kind": {"eq": "audio"}},
                "then": {"path": "Audio/{date:%Y/%Y-%m}", "tags": ["аудио"]},
            },
            {
                "id": "raw",
                "when": {"kind": {"eq": "raw"}},
                "then": {"path": "RAW/{date:%Y/%Y-%m-%d}", "tags": ["raw"]},
            },
            {
                "id": "default",
                "then": {"path": "Archive/{date:%Y/%Y-%m-%d}/{kind}"},
            },
        ],
        "fallback": dict(_FALLBACK),
    },
    "family": {
        "version": 1,
        "safety": dict(_SAFETY),
        "rules": [
            {
                "id": "documents",
                "stop": True,
                "when": {
                    "all": [
                        {"ai.scene_type": {"in": ["document", "receipt", "whiteboard", "screenshot"]}},
                        {"ai.confidence": {"gte": 0.75}},
                    ]
                },
                "then": {"path": "Docs/{date:%Y}/{ai.doc_kind|unknown}", "tags": ["документ"]},
            },
            {
                "id": "screenshots",
                "stop": True,
                "when": {"ai.scene_type": {"eq": "screenshot"}},
                "then": {"path": "Screenshots/{date:%Y}", "tags": ["скриншот"]},
            },
            {
                "id": "video",
                "stop": True,
                "when": {"kind": {"eq": "video"}},
                "then": {"path": "Family/{date:%Y}/Video", "tags": ["видео"]},
            },
            {
                "id": "default",
                "then": {"path": "Family/{date:%Y/%Y-%m-%d}", "tags": ["семья"]},
            },
        ],
        "fallback": dict(_FALLBACK),
    },
    "wedding": {
        "version": 1,
        "safety": dict(_SAFETY),
        "rules": [
            {
                "id": "jobs",
                "stop": True,
                "when": {
                    "all": [
                        {"ai.event_type": {"in": ["wedding", "concert", "sport"]}},
                        {"date": {"between": ["2025-05-01", "2025-10-31"]}},
                    ]
                },
                "then": {
                    "path": "Jobs/{date:%Y-%m-%d} {ai.event_type}/{camera.model|unknown}",
                    "tags": ["съёмка"],
                },
            },
            {
                "id": "by_camera",
                "then": {"path": "Jobs/{date:%Y-%m-%d}/{camera.model|unknown}"},
            },
        ],
        "fallback": dict(_FALLBACK),
    },
    "stock": {
        "version": 1,
        "safety": dict(_SAFETY),
        "rules": [
            {
                "id": "documents",
                "stop": True,
                "when": {"ai.scene_type": {"in": ["document", "receipt", "whiteboard"]}},
                "then": {"path": "Private/Documents/{date:%Y}", "tags": ["приватное"]},
            },
            {
                "id": "by_camera",
                "then": {"path": "Stock/{camera.make|unknown}/{date:%Y/%Y-%m}", "tags": ["сток"]},
            },
        ],
        "fallback": dict(_FALLBACK),
    },
    "video": {
        "version": 1,
        "safety": dict(_SAFETY),
        "rules": [
            {
                "id": "video",
                "stop": True,
                "when": {"kind": {"eq": "video"}},
                "then": {"path": "Projects/{date:%Y-%m}/Video", "tags": ["видео"]},
            },
            {
                "id": "audio",
                "stop": True,
                "when": {"kind": {"eq": "audio"}},
                "then": {"path": "Projects/{date:%Y-%m}/Audio", "tags": ["аудио"]},
            },
            {
                "id": "default",
                "then": {"path": "Projects/{date:%Y-%m}/Photo"},
            },
        ],
        "fallback": dict(_FALLBACK),
    },
}


def get_preset(name: str) -> dict[str, Any]:
    key = name.strip().lower()
    if key not in PRESETS:
        raise RuleError(f"Неизвестный пресет: {name}. Доступны: {', '.join(sorted(PRESETS))}")
    return copy.deepcopy(PRESETS[key])


__all__ = ["PRESETS", "get_preset"]
