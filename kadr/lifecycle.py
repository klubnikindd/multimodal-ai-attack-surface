"""Lifecycle sidecar (SEC-17, §11.2): process group, PDEATHSIG, shutdown, watchdog."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import SHUTDOWN_GRACE_SECONDS, SHUTDOWN_KILL_SECONDS, WATCHDOG_STALE_SECONDS


def _pdeathsig_preexec(parent_pid: int) -> Any:
    """PDEATHSIG + проверка, что родитель не умер до prctl (race)."""
    if sys.platform.startswith("linux"):
        def preexec() -> None:  # pragma: no cover - проверяется lifecycle-тестом на Linux
            import ctypes

            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            PR_SET_PDEATHSIG = 1
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
            if os.getppid() != parent_pid:
                os._exit(1)

        return preexec
    return None


def _drain_stream(stream: Any, lines: list[str], lock: threading.Lock) -> None:
    try:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip("\n")
            if not text:
                continue
            with lock:
                lines.append(text)
                del lines[:-1000]
    except (OSError, ValueError):
        return


def spawn_sidecar(argv: Sequence[str], *, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    """Запускает Engine в новой process group; дети/внуки остаются в группе.

    stdout/stderr объединены и вычитываются фоновым потоком, иначе PIPE
    заполняется и sidecar зависает. Handshake доступен через read_handshake().
    """
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "env": {
            **os.environ,
            "KADR_PARENT_PID": str(os.getpid()),
            **({"KADR_SIDECAR_PGID": "self"} if os.name != "nt" else {}),
            **(env or {}),
        },
    }
    if os.name == "nt":  # pragma: no cover - Windows
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        kwargs["start_new_session"] = True
        preexec = _pdeathsig_preexec(os.getpid())
        if preexec is not None:
            kwargs["preexec_fn"] = preexec
    process = subprocess.Popen(list(argv), **kwargs)
    if os.name == "nt":  # pragma: no cover - Windows
        # SEC-17/NFR-17: Job Object с kill-on-close — если родитель умрёт
        # (в т.ч. kill -9), ОС добьёт всё дерево. При невозможности (уже в job,
        # напр. в CI) остаётся taskkill из terminate_group.
        try:
            from .security.jobobject import assign_process_to_job, create_kill_on_close_job

            job = create_kill_on_close_job()
            if assign_process_to_job(job, process.pid):
                process.kadr_job = job  # type: ignore[attr-defined]
            else:
                from .security.jobobject import close_job

                close_job(job)
        except Exception:  # pragma: no cover - defensive
            pass
    lines: list[str] = []
    lock = threading.Lock()
    process.kadr_lines = lines  # type: ignore[attr-defined]
    process.kadr_lines_lock = lock  # type: ignore[attr-defined]
    assert process.stdout is not None
    threading.Thread(
        target=_drain_stream,
        args=(process.stdout, lines, lock),
        name="kadr-sidecar-drain",
        daemon=True,
    ).start()
    return process


def read_handshake(process: subprocess.Popen[Any], *, timeout: float = 10.0) -> dict[str, Any]:
    """Ждёт JSON-handshake в уже вычитываемом stdout sidecar."""
    deadline = time.monotonic() + timeout
    seen = 0
    while time.monotonic() < deadline:
        lines = getattr(process, "kadr_lines", [])
        lock = getattr(process, "kadr_lines_lock", None)
        if lock is not None:
            with lock:
                snapshot = list(lines)
        else:
            snapshot = list(lines)
        while seen < len(snapshot):
            line = snapshot[seen].strip()
            seen += 1
            if not line:
                continue
            try:
                return parse_handshake(line)
            except (ValueError, TypeError):
                continue
        if process.poll() is not None and seen >= len(snapshot):
            break
        time.sleep(0.05)
    raise TimeoutError("sidecar handshake timeout")


def pid_alive(pid: int) -> bool:
    """Жив ли процесс — без побочных эффектов (BLK-02, NFR-17).

    На Windows `os.kill(pid, 0)` НЕ является проверкой существования: для
    не-сигнальных значений CPython вызывает `TerminateProcess`, а
    `OpenProcess` по PID завершённого, но ещё не освобождённого процесса
    может успешно открыться. Поэтому используется Win32
    `OpenProcess` + `GetExitCodeProcess`.
    """
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - Windows
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:  # pragma: no cover - Windows
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def terminate_group(process: subprocess.Popen[Any], *, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
    """SIGTERM группе, затем SIGKILL; на Windows — taskkill /T с ожиданием."""
    if process.poll() is not None:
        return
    if os.name == "nt":  # pragma: no cover - Windows
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
            timeout=grace + SHUTDOWN_KILL_SECONDS,
        )
        # taskkill асинхронен по отношению к Popen: дожидаемся реального выхода,
        # иначе следующая же проверка живости увидит ещё живой процесс.
        try:
            process.wait(timeout=grace + SHUTDOWN_KILL_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=SHUTDOWN_KILL_SECONDS)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                pass
        return
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        process.terminate()
    try:
        process.wait(timeout=grace)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        process.kill()
    try:
        process.wait(timeout=SHUTDOWN_KILL_SECONDS)
    except subprocess.TimeoutExpired:  # pragma: no cover
        pass


def engine_shutdown(
    *,
    base_url: str,
    token: str,
    csrf_token: str | None = None,
    process: subprocess.Popen[Any] | None = None,
    grace: float = SHUTDOWN_GRACE_SECONDS,
) -> dict[str, Any]:
    """Единый путь завершения: POST /internal/shutdown → wait → kill group."""
    result: dict[str, Any] = {"shutdown_requested": False, "exit_code": None, "killed": False}
    if base_url.startswith("http://127.0.0.1") or base_url.startswith("http://localhost"):
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/internal/shutdown",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Kadr-CSRF": csrf_token or token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=grace) as response:  # noqa: S310 - localhost
                result["shutdown_requested"] = response.status == 200
        except Exception as exc:
            result["shutdown_error"] = f"{type(exc).__name__}: {exc}"
    if process is not None:
        try:
            result["exit_code"] = process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            result["killed"] = True
            terminate_group(process, grace=grace)
            result["exit_code"] = process.poll()
    return result


@contextmanager
def single_instance_lock(path: str | Path) -> Iterator[bool]:
    """Не даёт второму экземпляру поднять второй Engine."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = None
    acquired = False
    try:
        if os.name == "nt":  # pragma: no cover - Windows
            import msvcrt

            handle = open(lock_path, "a+")
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                acquired = True
            except OSError:
                acquired = False
        else:
            import fcntl

            handle = open(lock_path, "a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                acquired = False
        yield acquired
    finally:
        if handle is not None:
            try:
                if acquired:
                    if os.name == "nt":  # pragma: no cover - Windows
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            # Файл не удаляем: иначе возможна гонка inode (второй процесс держит
            # старый файл, третий создаёт новый и получает второй lock).


@dataclass(slots=True)
class Watchdog:
    """Самозавершение sidecar при смерти родителя/закрытии stdin."""

    parent_pid: int = 0
    stdin: Any = None
    interval: float = WATCHDOG_STALE_SECONDS
    pgid: int = 0
    _stop: threading.Event = field(
        default_factory=threading.Event, init=False, repr=False, compare=False
    )
    _thread: threading.Thread | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.parent_pid:
            self.parent_pid = int(os.environ.get("KADR_PARENT_PID") or os.getppid())
        if not self.pgid and os.environ.get("KADR_SIDECAR_PGID") == "self":
            # Безопасно только если sidecar запущен с start_new_session.
            self.pgid = os.getpgrp()
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kadr-watchdog", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            if os.getppid() != self.parent_pid:
                self._terminate_children()
                os._exit(0)
            if self.stdin is not None:
                try:
                    if self.stdin.closed:
                        self._terminate_children()
                        os._exit(0)
                except Exception:
                    pass

    def _terminate_children(self) -> None:
        if os.name == "nt":  # pragma: no cover - Windows
            return
        try:
            if self.pgid:
                os.killpg(self.pgid, signal.SIGTERM)
            else:
                # Не знаем группу sidecar: не рискуем убивать группу родителя.
                os.kill(os.getpid(), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()


def handshake_payload(port: int, token: str, pid: int, csrf_token: str | None = None) -> str:
    """stdout-handshake: токен/CSRF не попадают в URL/логи."""
    payload: dict[str, Any] = {"port": port, "token": token, "pid": pid, "ready": True}
    if csrf_token:
        payload["csrf_token"] = csrf_token
    return json.dumps(payload)


def parse_handshake(line: str) -> dict[str, Any]:
    data = json.loads(line)
    if not isinstance(data, dict) or "port" not in data or "token" not in data:
        raise ValueError("invalid sidecar handshake")
    port = int(data["port"])
    if not 1 <= port <= 65535:
        raise ValueError("invalid sidecar port")
    return data


def target_triple() -> str:
    env = os.environ.get("KADR_TARGET_TRIPLE")
    if env:
        return env
    try:
        completed = subprocess.run(
            ["rustc", "--print", "host-tuple"], capture_output=True, text=True, timeout=5, check=False
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    import platform

    arch = platform.machine().lower()
    system = platform.system().lower()
    if system == "linux":
        return f"{arch}-unknown-linux-gnu"
    if system == "darwin":
        return f"{arch}-apple-darwin"
    if system == "windows":
        return f"{arch}-pc-windows-msvc"
    return f"{arch}-unknown-{system}"


def sidecar_binary_name(base: str = "kadr-engine", *, triple: str | None = None) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    return f"{base}-{triple or target_triple()}{suffix}"


def wait_for_no_orphans(pids: Sequence[int], *, timeout: float = SHUTDOWN_KILL_SECONDS) -> list[int]:
    """Возвращает PID, которые всё ещё живы (для lifecycle-теста)."""
    deadline = time.monotonic() + timeout
    alive = list(pids)
    while time.monotonic() < deadline and alive:
        alive = [pid for pid in alive if pid_alive(pid)]
        if alive:
            time.sleep(0.05)
    return alive


__all__ = [
    "Watchdog",
    "engine_shutdown",
    "handshake_payload",
    "parse_handshake",
    "pid_alive",
    "sidecar_binary_name",
    "single_instance_lock",
    "read_handshake",
    "spawn_sidecar",
    "target_triple",
    "terminate_group",
    "wait_for_no_orphans",
]
