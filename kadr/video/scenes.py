"""Видео: scene-cut и ключевые кадры (FR-3.6, NFR-04).

SceneDetect (ContentDetector) режет видео на сцены, затем ffmpeg достаёт
K равномерно распределённых ключевых кадров. Кадры дальше идут в
фото-пайплайн (эмбеддинги + классификация) и дают обложку видео.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scene:
    start_seconds: float
    end_seconds: float


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def detect_scenes(
    video: str | Path,
    *,
    threshold: float = 27.0,
    min_scene_len: int = 15,
) -> list[Scene]:
    """Детектирует сцены ContentDetector-ом. Возвращает [(start, end)] в секундах."""
    from scenedetect import ContentDetector, SceneManager, open_video

    video_mgr = open_video(str(video))
    scene_mgr = SceneManager()
    scene_mgr.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_scene_len))
    scene_mgr.detect_scenes(video_mgr)
    return [Scene(s[0].seconds, s[1].seconds) for s in scene_mgr.get_scene_list()]


def keyframe_timestamps(scenes: list[Scene], k: int = 12) -> list[float]:
    """Равномерно раскладывает k кадров по суммарной длительности сцен."""
    if not scenes or k <= 0:
        return []
    start = scenes[0].start_seconds
    end = scenes[-1].end_seconds
    if end - start < 1e-3:
        return [start] * k
    return [start + (end - start) * (i + 0.5) / k for i in range(k)]


def extract_keyframes(
    video: str | Path,
    out_dir: str | Path,
    *,
    k: int = 12,
    width: int = 320,
    threshold: float = 27.0,
    min_scene_len: int = 15,
) -> list[Path]:
    """Scene-cut → k ключевых кадров (JPEG) через PyAV + Pillow. Возвращает пути."""
    import av
    from PIL import Image

    scenes = detect_scenes(video, threshold=threshold, min_scene_len=min_scene_len)
    if not scenes:
        return []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    container = av.open(str(video))
    try:
        stream = container.streams.video[0]
        fps = float(stream.average_rate) if stream.average_rate else 25.0
        indices = sorted({max(0, int(ts * fps)) for ts in keyframe_timestamps(scenes, k)})
        paths: list[Path] = []
        for i, frame in enumerate(container.decode(stream)):
            if i in indices:
                img = frame.to_image()
                img.thumbnail((width, width))
                dest = out / f"keyframe_{len(paths):03d}.jpg"
                img.save(dest, "JPEG")
                paths.append(dest)
                if len(paths) >= k:
                    break
            if indices and i > max(indices):
                break
        return paths
    finally:
        container.close()


__all__ = ["Scene", "detect_scenes", "extract_keyframes", "keyframe_timestamps"]
