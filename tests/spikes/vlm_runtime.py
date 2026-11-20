#!/usr/bin/env python3
"""P6-03: живой VLM-runtime — llama-server + LlamaCppClient → контракт §8.4.

Спавнит локальный llama-server (CPU или GPU), шлёт изображение через
`kadr.ai.vlm.LlamaCppClient` и проверяет ответ `validate_vlm_output`
(строгий JSON-контракт §8.4, thinking off, лимит токенов). Без бинаря/весов —
честный `not_measured`, это не падение.

Использование:
  KADR_GGUF=model.gguf KADR_MMPROJ=mmproj.gguf \\
    python tests/spikes/vlm_runtime.py --images .models-test/images-768 --ngl 999
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from kadr.ai.vlm import VLM_PROMPT, LlamaCppClient, validate_vlm_output  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_server() -> Path | None:
    env = os.environ.get("KADR_LLAMA_SERVER")
    if env and Path(env).exists():
        return Path(env)
    for candidate in (
        REPO_ROOT / ".models-test" / "llama-cuda-bin" / "llama-server.exe",
        REPO_ROOT / ".models-test" / "llama" / "llama-server.exe",
    ):
        if candidate.exists():
            return candidate
    return None


def _wait_health(base_url: str, timeout: float = 180.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - сервер ещё стартует
            time.sleep(1)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Живой VLM-runtime (P6-03)")
    parser.add_argument("--images", default=None)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--ngl", type=int, default=0, help="слои на GPU (999 = offload всех)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    server = _find_server()
    gguf = os.environ.get("KADR_GGUF")
    mmproj = os.environ.get("KADR_MMPROJ")
    missing = []
    if server is None:
        missing.append("llama-server не найден (KADR_LLAMA_SERVER или .models-test)")
    if not gguf or not Path(gguf).exists():
        missing.append("GGUF не указан (KADR_GGUF)")
    if not mmproj or not Path(mmproj).exists():
        missing.append("mmproj не указан (KADR_MMPROJ)")
    if args.images is None or not Path(args.images).is_dir():
        missing.append("--images не задан")
    if missing:
        payload = {"status": "not_measured", "reason": "; ".join(missing)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    images = sorted(
        p for p in Path(args.images).iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )[: args.count]
    port = _free_port()
    server_cmd = [
        str(server), "-m", str(gguf), "--mmproj", str(mmproj),
        "--host", "127.0.0.1", "--port", str(port), "-c", "2048",
    ]
    if args.ngl:
        server_cmd += ["-ngl", str(args.ngl)]
    process = subprocess.Popen(server_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
    base_url = f"http://127.0.0.1:{port}"
    try:
        healthy = _wait_health(base_url)
        if not healthy:
            print(json.dumps({"status": "error", "reason": "llama-server /health timeout"}, ensure_ascii=False))
            return 1
        client = LlamaCppClient(base_url, timeout=300)
        results = []
        for image in images:
            start = time.perf_counter()
            content = client.describe(image, VLM_PROMPT)
            elapsed = time.perf_counter() - start
            validation = validate_vlm_output(content)
            results.append(
                {
                    "image": image.name,
                    "latency_seconds": round(elapsed, 2),
                    "valid": validation.valid,
                    "anomalies": validation.anomalies,
                    "review_reasons": validation.review_reasons,
                    "output_keys": sorted(validation.output),
                }
            )
        payload = {"status": "measured", "base_url": base_url, "ngl": args.ngl, "results": results}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if args.out:
            Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
