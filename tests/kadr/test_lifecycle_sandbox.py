from __future__ import annotations

import os
import sys
import time

import pytest

from kadr.lifecycle import (
    handshake_payload,
    parse_handshake,
    read_handshake,
    sidecar_binary_name,
    single_instance_lock,
    spawn_sidecar,
    terminate_group,
    wait_for_no_orphans,
)
from kadr.security.sandbox import load_policy
from kadr.workers.client import run_worker


@pytest.mark.skipif(os.name == "nt", reason="process group semantics differ on Windows")
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
