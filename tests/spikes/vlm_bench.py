#!/usr/bin/env python3
"""Спайк D (ТЗ v3.1 §8.3.4, §13.1): бюджеты VLM — замер t_encode/t_decode.

Цифры §8.3 — ожидания до этого замера; UI обязан брать факты из
docs/BENCHMARKS.md и device_bench. Харнесс не выдаёт результат за продуктовый,
пока не выполнен на 4 классах устройств (§8.3.4).

Стенд (нормативно, §8.3.2): только *-Instruct GGUF + mmproj (ревизии запинены),
thinking off, max_output_tokens=384, изображения ≤1024 px (Lite-ночь ≤768),
параллелизм ≤ cores-1, питание от сети.

Замер «секунд на новый кадр» = wall-время однокартиночного прогона минус
baseline (загрузка модели + старт процесса, без изображения). Разрешение
задаётся заранее подготовленными изображениями (images-768 / images-1024).

Использование:
  KADR_LLAMA_BIN=./llama-mtmd-cli.exe KADR_GGUF=model.gguf KADR_MMPROJ=mmproj.gguf \\
    python tests/spikes/vlm_bench.py --images .models-test/images-768 --resolutions 768

Без бинаря/весов печатает status="not_measured" и причину.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_OUTPUT_TOKENS = 384
DEFAULT_IMAGES = 20
PROMPT = "Опиши изображение кратко: сцена, объекты, текст. Только JSON."


def _find_binary() -> Path | None:
    env = os.environ.get("KADR_LLAMA_BIN")
    if env and Path(env).exists():
        return Path(env)
    # Qwen3-VL — multimodal: нужен mtmd-клиент, а не текстовый llama-cli.
    for name in (
        "llama-mtmd-cli",
        "llama-mtmd-cli.exe",
        "llama-qwen2vl-cli",
        "llama-qwen2vl-cli.exe",
    ):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _find_model(flag: str) -> Path | None:
    env = os.environ.get(flag)
    if env and Path(env).exists():
        return Path(env)
    return None


def not_measured(reason: str) -> dict:
    return {
        "status": "not_measured",
        "reason": reason,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "device": os.environ.get("KADR_BENCH_DEVICE", "local"),
        "os": sys.platform,
        "cores": os.cpu_count(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Спайк D: замер бюджетов VLM")
    parser.add_argument("--images", default=None, help="каталог с тестовыми изображениями")
    parser.add_argument("--resolutions", nargs="+", type=int, default=[768])
    parser.add_argument("--count", type=int, default=DEFAULT_IMAGES)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    binary = _find_binary()
    gguf = _find_model("KADR_GGUF")
    mmproj = _find_model("KADR_MMPROJ")

    missing = []
    if binary is None:
        missing.append("llama.cpp mtmd-бинарь не найден (KADR_LLAMA_BIN или PATH)")
    if gguf is None:
        missing.append("GGUF не указан (KADR_GGUF)")
    if mmproj is None:
        missing.append("mmproj не указан (KADR_MMPROJ)")
    if args.images is None or not Path(args.images).is_dir():
        missing.append("каталог --images с изображениями не задан")
    if missing:
        payload = not_measured("; ".join(missing))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.out:
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    images = sorted(
        path
        for path in Path(args.images).iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )[: args.count]
    if not images:
        payload = not_measured(f"в {args.images} нет изображений")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    threads = str(max(1, (os.cpu_count() or 2) - 1))
    base_cmd = [
        str(binary), "-m", str(gguf), "--mmproj", str(mmproj),
        "-p", "ok", "-n", "8", "--temp", "0", "--threads", threads,
    ]
    base_times: list[float] = []
    for _ in range(2):
        start = time.perf_counter()
        try:
            subprocess.run(base_cmd, capture_output=True, text=True, timeout=600)  # noqa: S603
        except subprocess.TimeoutExpired:
            base_times.append(600.0)
            continue
        base_times.append(time.perf_counter() - start)
    baseline_seconds = min(base_times)

    results: list[dict] = []
    for resolution in args.resolutions:
        timings: list[float] = []
        for image in images:
            argv_cmd = [
                str(binary), "-m", str(gguf), "--mmproj", str(mmproj),
                "--image", str(image),
                "-p", PROMPT, "-n", str(MAX_OUTPUT_TOKENS),
                "--temp", "0", "--threads", threads,
            ]
            start = time.perf_counter()
            try:
                subprocess.run(argv_cmd, capture_output=True, text=True, timeout=600)  # noqa: S603
            except subprocess.TimeoutExpired:
                timings.append(600.0)
                continue
            timings.append(max(0.0, time.perf_counter() - start - baseline_seconds))
        results.append(
            {
                "resolution_px": resolution,
                "images": len(images),
                "baseline_seconds": round(baseline_seconds, 2),
                "seconds_per_new_image_median": round(statistics.median(timings), 2),
                "seconds_per_new_image_p95": round(
                    statistics.quantiles(timings, n=20)[18]
                    if len(timings) >= 20
                    else max(timings),
                    2,
                ),
                "model": str(gguf),
                "mmproj": str(mmproj),
                "binary": str(binary),
                "thinking": False,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
        )
    payload = {
        "status": "measured",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "device": os.environ.get("KADR_BENCH_DEVICE", "local"),
        "os": sys.platform,
        "cores": os.cpu_count(),
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
