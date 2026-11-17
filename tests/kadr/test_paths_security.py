from __future__ import annotations

import pytest

from kadr.errors import PathSafetyError
from kadr.security.paths import (
    is_network_path,
    normalize_rel_path,
    safe_join,
    sanitize_segment,
)


def test_sanitize_segment_windows_reserved_and_forbidden():
    assert sanitize_segment("CON") == "_CON"
    assert sanitize_segment("CON.txt") == "_CON.txt"
    assert sanitize_segment("a<b>c:d") == "a_b_c_d"
    assert sanitize_segment("..") == "_"
    assert sanitize_segment("  name.  ") == "name"
    assert len(sanitize_segment("x" * 500)) == 64


@pytest.mark.parametrize(
    "payload",
    [
        "../etc/passwd",
        "..\\..\\windows\\system32",
        "/absolute/path",
        "C:\\Windows\\System32",
        "\\\\server\\share\\file",
        "dir/file:stream",
        "a/../../b",
        "bad\x00name",
    ],
)
def test_path_traversal_payloads_are_rejected(payload):
    with pytest.raises(PathSafetyError):
        normalize_rel_path(payload)


def test_safe_join_stays_inside_root(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    result = safe_join(root, "2024/07/photo.jpg")
    assert result == (root / "2024/07/photo.jpg").resolve()
    with pytest.raises(PathSafetyError):
        safe_join(root, "../outside.jpg")


def test_symlink_component_is_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSafetyError):
        safe_join(root, "link/photo.jpg")


def test_network_path_detection():
    assert is_network_path("//server/share")
    assert is_network_path("\\\\server\\share")
    assert not is_network_path("/local/path")
