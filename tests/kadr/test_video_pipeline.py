"""Видео-конвейер (FR-3.6): сцены + ключевые кадры через run_video_pipeline."""

from __future__ import annotations

from pathlib import Path


def _make_video(dst: Path) -> None:
    import av

    container = av.open(str(dst), mode="w")
    stream = container.add_stream("mpeg4", rate=25)
    stream.width = 320
    stream.height = 240
    stream.pix_fmt = "yuv420p"
    for y, u, v in [(76, 90, 240), (29, 240, 110)]:  # две «сцены»
        for _ in range(50):
            frame = av.VideoFrame(320, 240, "yuv420p")
            frame.planes[0].update(bytes([y]) * (320 * 240))
            frame.planes[1].update(bytes([u]) * (160 * 120))
            frame.planes[2].update(bytes([v]) * (160 * 120))
            for packet in stream.encode(frame):
                container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def test_video_pipeline_scenes(tmp_path: Path) -> None:
    from kadr.ai.video_pipeline import run_video_pipeline
    from kadr.catalog.repository import Catalog
    from kadr.config import Settings
    from kadr.scanner import scan_root

    media = tmp_path / "media"
    media.mkdir()
    v = media / "two.mp4"
    _make_video(v)

    settings = Settings(home=tmp_path / ".kadr", library_root=tmp_path / "lib")
    catalog = Catalog(settings.catalog_path)
    scan_root(catalog, media, settings)
    assets = catalog.list_assets()
    assert len(assets) == 1
    asset_id = int(assets[0]["id"])

    result = run_video_pipeline(catalog, asset_id, v, run_asr=False)
    assert result["scenes"] >= 1
    assert result["keyframes"] >= 1
    out = catalog.get_ai_output(asset_id, "video_scenes")
    assert out is not None
