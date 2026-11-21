"""FR-3.6: scene-cut + ключевые кадры (PySceneDetect + PyAV)."""

from __future__ import annotations

from pathlib import Path

from kadr.video.scenes import detect_scenes, extract_keyframes


def _make_two_scene_video(dst: Path) -> None:
    import av

    container = av.open(str(dst), mode="w")
    stream = container.add_stream("mpeg4", rate=25)
    stream.width = 320
    stream.height = 240
    stream.pix_fmt = "yuv420p"
    # две «сцены»: красная → синяя (резкий сдвиг цветовой гистограммы)
    for y, u, v in [(76, 90, 240), (29, 240, 110)]:
        for _ in range(50):  # 2 секунды при 25 fps
            frame = av.VideoFrame(320, 240, "yuv420p")
            frame.planes[0].update(bytes([y]) * (320 * 240))
            frame.planes[1].update(bytes([u]) * (160 * 120))
            frame.planes[2].update(bytes([v]) * (160 * 120))
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_detect_scenes(tmp_path: Path) -> None:
    video = tmp_path / "two.mp4"
    _make_two_scene_video(video)
    scenes = detect_scenes(video, threshold=27.0, min_scene_len=15)
    assert len(scenes) >= 2


def test_extract_keyframes(tmp_path: Path) -> None:
    video = tmp_path / "two.mp4"
    _make_two_scene_video(video)
    frames = extract_keyframes(video, tmp_path / "frames", k=4, width=160)
    assert len(frames) == 4
    assert all(f.exists() and f.stat().st_size > 0 for f in frames)
