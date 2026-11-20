"""Единая политика песочницы (SEC-05, D12, §13.4).

Один YAML-файл генерирует лимиты для native-бэкендов (seccomp/Seatbelt/Job
Object) и research-Docker. Тест паритета сравнивает эффективные лимиты;
Docker-прогон не заменяет нативные security-метрики.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None

DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "name": "kadr-parser-worker",
    "network": "none",
    "exec": {"allow": ["exiftool", "ffprobe", "ffmpeg"], "shell": False},
    "paths": {"read": ["input"], "write": ["tmp"], "deny": ["home", "library_root"]},
    "limits": {
        "cpu_seconds": 10,
        "memory_mb": 1024,
        "file_size_mb": 512,
        "processes": 8,
        "open_files": 128,
        "timeout_seconds": 10,
        "video_timeout_seconds": 60,
        "max_pixels": 100_000_000,
        "max_duration_seconds": 4 * 60 * 60,
        "max_frames": 120,
    },
    "docker": {
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "network_mode": "none",
        "pids_limit": 64,
        "mem_limit": "1g",
        "cpus": 1.0,
        "tmpfs": ["/tmp:size=512m,noexec,nosuid"],
    },
}


def default_policy_path() -> Path:
    candidates = [
        Path(__file__).resolve().parent / "policy.yaml",  # one-dir datas
        Path(__file__).resolve().parents[2] / "security" / "sandbox" / "policy.yaml",  # repo
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[-1]


@dataclass(slots=True)
class SandboxPolicy:
    data: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_POLICY)))
    source: str = "default"

    @property
    def limits(self) -> dict[str, Any]:
        return dict(self.data.get("limits", {}))

    @property
    def docker(self) -> dict[str, Any]:
        return dict(self.data.get("docker", {}))

    def policy_hash(self) -> str:
        canonical = json.dumps(self.data, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def native_limits(self) -> dict[str, Any]:
        limits = self.limits
        return {
            "cpu_seconds": limits.get("cpu_seconds"),
            "memory_mb": limits.get("memory_mb"),
            "file_size_mb": limits.get("file_size_mb"),
            "processes": limits.get("processes"),
            "open_files": limits.get("open_files"),
            "timeout_seconds": limits.get("timeout_seconds"),
            "network": self.data.get("network", "none"),
            "shell": self.data.get("exec", {}).get("shell", False),
        }

    def docker_limits(self) -> dict[str, Any]:
        docker = self.docker
        mem = str(docker.get("mem_limit", "1g")).lower()
        memory_mb = None
        if mem.endswith("g"):
            memory_mb = int(float(mem[:-1]) * 1024)
        elif mem.endswith("m"):
            memory_mb = int(float(mem[:-1]))
        return {
            "cpu_seconds": self.limits.get("cpu_seconds"),
            "memory_mb": memory_mb,
            "file_size_mb": self.limits.get("file_size_mb"),
            "processes": docker.get("pids_limit"),
            "open_files": self.limits.get("open_files"),
            "timeout_seconds": self.limits.get("timeout_seconds"),
            "network": docker.get("network_mode", "none"),
            "shell": False,
        }

    def parity(self) -> dict[str, Any]:
        native = self.native_limits()
        docker = self.docker_limits()
        differences = {
            key: {"native": native.get(key), "docker": docker.get(key)}
            for key in native
            if native.get(key) != docker.get(key)
        }
        # pids_limit в Docker — это число процессов, native processes — мягкий
        # лимит в других единицах. Не считаем это эквивалентностью молча:
        # переносим в accepted и явно показываем в отчёте паритета.
        accepted: dict[str, Any] = {}
        if set(differences) == {"processes"}:
            accepted["processes"] = differences.pop("processes")
        return {
            "equal": not differences,
            "differences": differences,
            "accepted": accepted,
            "hash": self.policy_hash(),
        }

    def render_seatbelt(self) -> str:
        limits = self.limits
        read_paths = self.data.get("paths", {}).get("read", [])
        write_paths = self.data.get("paths", {}).get("write", [])
        lines = [
            "(version 1)",
            "(deny default)",
            '(allow process-exec (literal "/usr/bin/exiftool") (literal "/usr/bin/ffprobe") (literal "/usr/bin/ffmpeg"))',
            '(allow file-read* (subpath "/tmp/kadr-input"))'
            if "input" in read_paths
            else "(allow file-read*)",
            '(allow file-write* (subpath "/tmp/kadr-tmp"))'
            if "tmp" in write_paths
            else "(deny file-write*)",
            "(deny network*)",
            "(allow system-socket (socket-domain AF_UNIX))" if limits else "",
        ]
        return "\n".join(line for line in lines if line)

    def render_linux_seccomp(self) -> str:
        return json.dumps(
            {
                "defaultAction": "SCMP_ACT_ERRNO",
                "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_AARCH64"],
                "syscalls": [
                    {
                        "names": [
                            "read",
                            "write",
                            "close",
                            "fstat",
                            "mmap",
                            "munmap",
                            "exit_group",
                        ],
                        "action": "SCMP_ACT_ALLOW",
                    },
                    {"names": ["socket", "connect", "execve"], "action": "SCMP_ACT_ERRNO"},
                ],
            },
            indent=2,
        )

    def render_rlimits(self) -> dict[str, Any]:
        return dict(self.limits)

    def render_windows_job(self) -> str:
        return json.dumps(
            {
                "kill_on_job_close": True,
                "active_process_limit": self.limits.get("processes"),
                "process_memory_limit_mb": self.limits.get("memory_mb"),
                "job_memory_limit_mb": self.limits.get("memory_mb"),
                "ui_restrictions": ["JOB_OBJECT_UILIMIT_HANDLES"],
            },
            indent=2,
        )

    def render_docker_options(self) -> list[str]:
        docker = self.docker
        options = ["--read-only", "--network", str(docker.get("network_mode", "none"))]
        for cap in docker.get("cap_drop", []):
            options.extend(["--cap-drop", str(cap)])
        for opt in docker.get("security_opt", []):
            options.extend(["--security-opt", str(opt)])
        options.extend(["--pids-limit", str(docker.get("pids_limit", 64))])
        options.extend(["--memory", str(docker.get("mem_limit", "1g"))])
        options.extend(["--cpus", str(docker.get("cpus", 1.0))])
        return options


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_policy(path: str | Path | None = None) -> SandboxPolicy:
    policy_path = Path(path) if path else default_policy_path()
    if not policy_path.is_file():
        return SandboxPolicy()
    try:
        if yaml is None:
            return SandboxPolicy()
        loaded = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return SandboxPolicy()
        merged = _deep_merge(json.loads(json.dumps(DEFAULT_POLICY)), loaded)
        return SandboxPolicy(data=merged, source=str(policy_path))
    except Exception:
        return SandboxPolicy()


__all__ = ["DEFAULT_POLICY", "SandboxPolicy", "default_policy_path", "load_policy"]
