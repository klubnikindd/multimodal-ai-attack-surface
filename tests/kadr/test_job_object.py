"""SEC-17/NFR-17: Job Object kill-on-close убивает дерево процессов (Windows)."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from kadr.lifecycle import pid_alive


def test_job_object_kill_on_close_kills_descendants():
    pytest.importorskip("ctypes")
    if os.name != "nt":  # pragma: no cover - Windows-only
        pytest.skip("Job Object доступен только на Windows")

    from kadr.security.jobobject import (
        assign_process_to_job,
        close_job,
        create_kill_on_close_job,
    )

    job = create_kill_on_close_job()
    # Процесс-«внук»: родитель спавнит ребёнка, оба в job.
    parent = subprocess.Popen(  # noqa: S603 - фиксированный argv, без shell
        [sys.executable, "-c", "import subprocess,sys,time; "
         "subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); "
         "time.sleep(120)"]
    )
    try:
        time.sleep(0.5)
        assert assign_process_to_job(job, parent.pid) is True
        # Перечислить внука надёжно сложно; закрытие job убивает всё, что в нём.
        close_job(job)  # = смерть владельца handle'а
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and pid_alive(parent.pid):
            time.sleep(0.1)
        assert pid_alive(parent.pid) is False
    finally:
        if parent.poll() is None:
            parent.kill()
