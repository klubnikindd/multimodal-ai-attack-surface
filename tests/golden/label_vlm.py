# -*- coding: utf-8 -*-
"""VLM-разметка golden-датасета: Qwen3-VL-2B → scene/event/quality (GPU, CUDA)."""
import json, socket, subprocess, sys, time, urllib.request
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO))
from kadr.ai.vlm import LlamaCppClient, build_vlm_prompt, validate_vlm_output  # noqa: E402
from kadr.constants import VLM_NIGHT_IMAGE_MAX_SIDE  # noqa: E402

ROOT = REPO / "data/golden/v1/inputs"
GGUF = REPO / ".models-test/model.gguf"
MMPROJ = REPO / ".models-test/mmproj.gguf"
SERVER = REPO / ".models-test/llama-cuda-bin/llama-server.exe"
CHECKPOINT = REPO / "data/golden/v1/vlm_labels.json"
MAX_SIDE = VLM_NIGHT_IMAGE_MAX_SIDE  # 768 px

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def wait_health(base_url, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(2)
    return False

photos = sorted(p for p in ROOT.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
done = json.loads(CHECKPOINT.read_text(encoding="utf-8")) if CHECKPOINT.exists() else {}
todo = [p for p in photos if p.name not in done]
print(f"photos={len(photos)} done={len(done)} todo={len(todo)}", flush=True)
if not todo:
    print("ALL_DONE", flush=True)
    sys.exit(0)

port = free_port()
cmd = [str(SERVER), "-m", str(GGUF), "--mmproj", str(MMPROJ),
       "--host", "127.0.0.1", "--port", str(port), "-c", "2048", "-ngl", "999"]
proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base_url = f"http://127.0.0.1:{port}"
try:
    if not wait_health(base_url):
        print(json.dumps({"error": "llama-server /health timeout"}), flush=True)
        sys.exit(1)
    client = LlamaCppClient(base_url, timeout=300)
    prompt = build_vlm_prompt()
    t0 = time.monotonic()
    for i, p in enumerate(todo):
        try:
            content = client.describe(p, prompt, max_side=MAX_SIDE)
            val = validate_vlm_output(content)
            o = val.output
            done[p.name] = {
                "scene": o.get("scene_type"),
                "setting": o.get("setting"),
                "event": o.get("event_type"),
                "quality": o.get("quality_issues") or [],
                "confidence": o.get("confidence"),
                "caption_ru": o.get("caption_ru"),
                "valid": val.valid,
                "anomalies": val.anomalies,
            }
        except Exception as e:
            done[p.name] = {"error": str(e)[:200]}
        if (i + 1) % 10 == 0 or (i + 1) == len(todo):
            CHECKPOINT.write_text(json.dumps(done, ensure_ascii=False, indent=2), encoding="utf-8")
            elapsed = time.monotonic() - t0
            rate = (len(done) - (len(photos) - len(todo))) / elapsed if elapsed > 0 else 0
            print(f"progress {len(done)}/{len(photos)} elapsed={elapsed:.0f}s", flush=True)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

errs = sum(1 for v in done.values() if "error" in v)
valid = sum(1 for v in done.values() if v.get("valid"))
print(f"DONE total={len(done)} valid={valid} errors={errs}", flush=True)
