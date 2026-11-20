"""Контент-адресация (blake3) и перцептивные хэши."""

from __future__ import annotations

import hashlib
import importlib
import math
import os
from pathlib import Path
from typing import Any

from .constants import HASH_CHUNK, MAX_FILE_HASH_BYTES

_blake3: Any = None
try:  # pragma: no cover - зависит от окружения
    _blake3 = importlib.import_module("blake3")
except Exception:  # pragma: no cover
    _blake3 = None


def hash_bytes(data: bytes) -> str:
    if _blake3 is not None:
        return "blake3:" + _blake3.blake3(data).hexdigest()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_file(path: str | Path, *, max_bytes: int = MAX_FILE_HASH_BYTES) -> str:
    """Потоковый хэш файла (не загружает гигабайты в память)."""
    if _blake3 is not None:
        h = _blake3.blake3()
        prefix = "blake3:"
    else:  # pragma: no cover - fallback для окружений без blake3
        h = hashlib.sha256()
        prefix = "sha256:"
    total = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(HASH_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Файл больше лимита хэширования: {path}")
            h.update(chunk)
    return prefix + h.hexdigest()


def verify_hash(path: str | Path, expected: str) -> bool:
    try:
        actual = hash_file(path)
    except (OSError, ValueError):
        return False
    if expected and not expected.startswith(("blake3:", "sha256:")):
        expected = "blake3:" + expected if _blake3 is not None else "sha256:" + expected
    return actual == expected


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8", errors="replace"))


_DCT_SIZE = 32
# Косинусная матрица считается один раз при импорте, а не 65k раз на картинку.
_DCT_MATRIX = [
    [math.cos(math.pi / _DCT_SIZE * (i + 0.5) * k) for i in range(_DCT_SIZE)]
    for k in range(_DCT_SIZE)
]


def _dct_1d(values: list[float]) -> list[float]:
    """DCT-II через предвычисленную косинусную матрицу (O(n²) без math.cos)."""
    return [sum(weight * value for weight, value in zip(row, values, strict=False)) for row in _DCT_MATRIX]


def perceptual_hash(path: str | Path) -> str | None:
    """pHash 64 бита через DCT 32×32. Без numpy, терпимо к JPEG-пережатию."""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return None
    try:
        Image.MAX_IMAGE_PIXELS = 64_000_000
        with Image.open(path) as img:
            if img.width * img.height > 64_000_000:
                return None
            gray = img.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
            pixels = list(gray.getdata())
    except Exception:
        return None
    matrix = [pixels[i * 32 : (i + 1) * 32] for i in range(32)]
    rows = [_dct_1d([float(x) for x in row]) for row in matrix]
    cols = []
    for x in range(32):
        cols.append(_dct_1d([rows[y][x] for y in range(32)]))
    top = []
    for y in range(8):
        for x in range(8):
            top.append(cols[x][y])
    # top уже содержит ровно 64 (8×8) коэффициента. DC участвует в битах,
    # но исключён из медианы, чтобы не сдвигать порог.
    values = top
    median = sorted(values[1:])[len(values[1:]) // 2]
    bits = 0
    for i, value in enumerate(values):
        if value > median:
            bits |= 1 << (63 - i)
    return f"{bits:016x}"


def difference_hash(path: str | Path) -> str | None:
    """dHash 64 бита — быстрый кандидат для почти-дублей."""
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return None
    try:
        Image.MAX_IMAGE_PIXELS = 64_000_000
        with Image.open(path) as img:
            if img.width * img.height > 64_000_000:
                return None
            gray = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = list(gray.getdata())
    except Exception:
        return None
    bits = 0
    index = 0
    for row in range(8):
        for col in range(8):
            left = pixels[row * 9 + col]
            right = pixels[row * 9 + col + 1]
            if left > right:
                bits |= 1 << (63 - index)
            index += 1
    return f"{bits:016x}"


def hamming_distance_hex(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def file_ext(path: str | Path) -> str:
    return os.path.splitext(str(path))[1].lower()
