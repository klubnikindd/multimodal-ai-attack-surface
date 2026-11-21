"""Zero-egress (SEC-12): воркер не ходит в сеть; политика network=none."""

from __future__ import annotations

import socket

import pytest


def test_policy_network_none() -> None:
    from kadr.security.sandbox import load_policy

    policy = load_policy()
    assert policy.data.get("network") == "none"


def test_worker_denies_network() -> None:
    import kadr.workers.parser_worker as pw

    orig = socket.socket
    try:
        pw._deny_network()
        with pytest.raises(PermissionError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(PermissionError):
            socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    finally:
        socket.socket = orig


def test_worker_metadata_still_works_after_deny(tmp_path) -> None:
    """После _deny_network метаданные (без сети) всё ещё извлекаются."""
    import kadr.workers.parser_worker as pw

    from PIL import Image

    p = tmp_path / "t.jpg"
    Image.new("RGB", (64, 64), (100, 120, 140)).save(p)
    orig = socket.socket
    try:
        pw._deny_network()
        # extract_metadata не использует сеть — не должно падать
        meta = pw.extract_metadata(str(p))
        assert meta is not None
    finally:
        socket.socket = orig
