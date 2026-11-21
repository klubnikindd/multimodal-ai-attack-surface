from __future__ import annotations

import os
import sys
import time

import pytest

from kadr.lifecycle import (
    handshake_payload,
    parse_handshake,
    pid_alive,
    read_handshake,
    sidecar_binary_name,
    single_instance_lock,
    spawn_sidecar,
    terminate_group,
    wait_for_no_orphans,
)
from kadr.security.sandbox import load_policy
from kadr.workers.client import run_worker


def test_process_group_is_terminated_and_no_orphans():
    process = spawn_sidecar([sys.executable, "-c", "import time; time.sleep(60)"])
    pid = process.pid
    try:
        time.sleep(0.1)
        terminate_group(process, grace=0.5)
        assert process.poll() is not None
        assert wait_for_no_orphans([pid], timeout=2.0) == []
    finally:
        if process.poll() is None:
            process.kill()


def test_pid_alive_reflects_real_process_state():
    """BLK-02: живость проверяется без побочных эффектов и на Windows."""
    process = spawn_sidecar([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert pid_alive(process.pid) is True
        terminate_group(process, grace=0.5)
        assert pid_alive(process.pid) is False
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    assert pid_alive(-1) is False


def test_single_instance_lock(tmp_path):
    path = tmp_path / "engine.lock"
    with single_instance_lock(path) as acquired:
        assert acquired is True
        with single_instance_lock(path) as second:
            assert second is False
    with single_instance_lock(path) as again:
        assert again is True


def test_handshake_and_target_naming():
    line = handshake_payload(43210, "secret-token", 123)
    parsed = parse_handshake(line)
    assert parsed["port"] == 43210
    assert parsed["token"] == "secret-token"
    assert "kadr-engine-" in sidecar_binary_name(triple="x86_64-unknown-linux-gnu")
    with pytest.raises(ValueError):
        parse_handshake('{"port": 0, "token": "x"}')


def test_sandbox_policy_and_renderers():
    policy = load_policy()
    assert policy.limits["memory_mb"] > 0
    assert policy.policy_hash()
    assert "deny default" in policy.render_seatbelt()
    assert "SCMP_ACT_ERRNO" in policy.render_linux_seccomp()
    assert "kill_on_job_close" in policy.render_windows_job()
    options = policy.render_docker_options()
    assert "--read-only" in options
    assert "none" in options


@pytest.mark.skipif(os.name == "nt", reason="process group semantics differ on Windows")
def test_sidecar_stdout_is_drained_and_handshake_readable():
    code = (
        "import json,time;"
        "print(json.dumps({'port': 40123, 'token': 'tok', 'pid': 1, 'ready': True}), flush=True);"
        "print('x' * 200000, flush=True);"
        "time.sleep(2)"
    )
    process = spawn_sidecar([sys.executable, "-c", code])
    try:
        handshake = read_handshake(process, timeout=5)
        assert handshake["port"] == 40123
        assert handshake["token"] == "tok"
    finally:
        terminate_group(process, grace=1.0)


def test_parser_worker_receives_policy_limits(tmp_path):
    from PIL import Image

    image = tmp_path / "image.jpg"
    Image.new("RGB", (16, 16), color=(10, 20, 30)).save(image)
    payload = run_worker("metadata", image)
    assert payload["ok"] is True
    assert payload["limits_applied"]["memory_mb"] == load_policy().limits["memory_mb"]
    assert payload["metadata"]["width"] == 16


def test_parser_worker_enforces_pixel_limit(tmp_path):
    """SEC-09/T09: превышение max_pixels должно отклонить файл, а не дать частичные метаданные."""
    from PIL import Image

    from kadr.workers.client import WorkerError

    image = tmp_path / "big.jpg"
    Image.new("RGB", (400, 400), color=(0, 0, 0)).save(image)  # 160K пикселей
    with pytest.raises(WorkerError) as exc_info:
        run_worker("metadata", image, limits={"max_pixels": 1000})
    message = str(exc_info.value).lower()
    assert "bomb" in message or "pixel" in message


def test_sandbox_parity_exposes_accepted_differences():
    parity = load_policy().parity()
    assert set(parity) >= {"equal", "differences", "accepted", "hash"}
    # Разные единицы процессов не маскируются: они попадают в accepted, а не
    # просто стираются из отчёта.
    assert isinstance(parity["accepted"], dict)
    if "processes" in parity["accepted"]:
        assert "processes" not in parity["differences"]


def test_worker_output_is_read_with_a_limit():
    import io

    from kadr.workers.client import WorkerError, _read_limited

    assert _read_limited(io.BytesIO(b"abc"), 3, "stdout") == b"abc"
    with pytest.raises(WorkerError):
        _read_limited(io.BytesIO(b"abcd"), 3, "stdout")


def test_serve_sidecar_emits_handshake_and_health():
    """Sidecar-режим печатает stdout-handshake после готовности /health (§11.2)."""
    pytest.importorskip("uvicorn")
    import json
    import subprocess
    import urllib.request

    proc = subprocess.Popen(
        [sys.executable, "-m", "kadr", "serve", "--sidecar"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    handshake = None
    deadline = time.monotonic() + 20.0
    try:
        while time.monotonic() < deadline and handshake is None:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if "port" in data and "token" in data and data.get("ready"):
                    handshake = data
            except ValueError:
                pass
        assert handshake is not None, "sidecar handshake not emitted"
        assert 1 <= handshake["port"] <= 65535
        assert handshake["token"]
        with urllib.request.urlopen(
            f"http://127.0.0.1:{handshake['port']}/health", timeout=5
        ) as response:
            assert response.status == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
