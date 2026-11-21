"""Видео-конвейер: сцены и ключевые кадры (FR-3.6)."""

from .scenes import Scene, detect_scenes, extract_keyframes, keyframe_timestamps

__all__ = ["Scene", "detect_scenes", "extract_keyframes", "keyframe_timestamps"]
