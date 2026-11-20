"""SEC-05/T05: malformed-файлы не роняют процесс (parser-worker изолирован)."""

from __future__ import annotations

from kadr.workers.client import WorkerError, run_worker


def test_malformed_files_do_not_crash_parent(tmp_path):
    cases = {
        "truncated.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 32,
        "fake.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
        "garbage.bin": b"\x00" * 64,
        "empty.jpg": b"",
        "huge_claim.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (b"\xff" * 64),
    }
    for name, data in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        # Либо корректный dict (метаданные/parse_errors), либо WorkerError —
        # главное: родитель жив и не упал с необработанным исключением.
        try:
            result = run_worker("metadata", path)
            assert isinstance(result, dict)
        except WorkerError:
            pass
