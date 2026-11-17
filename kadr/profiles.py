"""Авто-детект профиля качества (D2/§8.2) и ETA-калибровка."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import ProfileInfo


def ram_gb() -> float:
    try:
        if Path("/proc/meminfo").is_file():
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    kib = int(line.split()[1])
                    return round(kib / 1024 / 1024, 1)
        if platform.system() == "Darwin":  # pragma: no cover - macOS
            completed = subprocess.run(
                ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5, check=False
            )
            if completed.stdout.strip().isdigit():
                return round(int(completed.stdout.strip()) / 1024**3, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):  # pragma: no cover
        return 8.0


def physical_cores() -> int:
    count = os.cpu_count() or 4
    try:
        if Path("/proc/cpuinfo").is_file():
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="ignore")
            pairs = set()
            for line in text.splitlines():
                if line.startswith("physical id") or line.startswith("core id"):
                    pairs.add(line.strip())
            # Не все платформы дают physical id; используем cpu_count как оценку.
            if len(pairs) >= 2:
                return max(1, len(pairs) // 2)
    except OSError:
        pass
    return count


def detect_gpu() -> tuple[str | None, float | None]:
    mac = platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
    if mac:
        return "apple-metal", None
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            completed = subprocess.run(
                [
                    executable,
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            line = (completed.stdout or "").strip().splitlines()
            if line:
                name, _, memory = line[0].partition(",")
                try:
                    vram = round(float(memory.strip()) / 1024, 1)
                except ValueError:
                    vram = None
                return name.strip(), vram
        except (OSError, subprocess.SubprocessError):
            pass
    return None, None


def detect_profile(override: str | None = None) -> ProfileInfo:
    ram = ram_gb()
    cores = physical_cores()
    gpu, vram = detect_gpu()
    apple = platform.system() == "Darwin" and platform.machine().lower() in {"arm64", "aarch64"}
    reasons: list[str] = []
    if override:
        profile = override
        reasons.append("profile override")
    elif ram < 8 or cores < 4:
        profile = "lite-forced"
        reasons.append("RAM < 8 GB or cores < 4")
    elif ram >= 32 and vram is not None and vram >= 12:
        profile = "pro-plus"
        reasons.append("RAM >= 32 GB and VRAM >= 12 GB")
    elif vram is not None and vram >= 6 and ram >= 16:
        profile = "pro"
        reasons.append("discrete GPU >= 6 GB VRAM and RAM >= 16 GB")
    elif apple and ram >= 16:
        profile = "balanced"
        reasons.append("Apple Silicon with >= 16 GB unified memory")
    else:
        profile = "lite"
        reasons.append("CPU-only or integrated GPU")
    vlm_main = profile in {"balanced", "pro", "pro-plus"}
    nightly = profile == "lite"
    return ProfileInfo(
        profile=profile,
        ram_gb=ram,
        cores=cores,
        gpu=gpu,
        vram_gb=vram,
        apple_silicon=apple,
        vlm_in_main_flow=vlm_main,
        nightly_vlm_available=nightly,
        reasons=reasons,
    )


def default_ms_per_item(task: str = "vlm_2b_q4") -> float:
    """Консервативная оценка до M0-замера; источник фактов — device_bench."""
    return {
        "vlm_2b_q4": 18_000.0,
        "vlm_4b_q4": 45_000.0,
        "embeddings_int8": 100.0,
        "ocr_onnx": 300.0,
    }.get(task, 1000.0)


def eta_seconds(count: int, ms_per_item: float) -> int:
    return max(0, int(round(count * max(0.0, ms_per_item) / 1000.0)))


def human_eta(seconds: int) -> str:
    if seconds <= 0:
        return "0 с"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} ч {minutes:02d} мин"
    if minutes:
        return f"{minutes} мин {secs:02d} с"
    return f"{secs} с"


def device_id() -> str:
    return f"{platform.system().lower()}-{platform.machine().lower()}-{physical_cores()}c-{int(ram_gb())}g"


def benchmark_payload(profile: ProfileInfo) -> dict[str, Any]:
    return {
        "device_id": device_id(),
        "os": platform.platform(),
        "cores": profile.cores,
        "ram_gb": profile.ram_gb,
        "gpu": profile.gpu,
        "vram_gb": profile.vram_gb,
        "profile": profile.profile,
        "python": platform.python_version(),
    }
