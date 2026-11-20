import pytest

from attack_surface.metadata import extract_metadata


def test_extract_metadata_returns_dict(tmp_path):
    # Create a temporary file
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Hello world")
    result = extract_metadata(str(file_path))
    assert isinstance(result, dict)
    assert result["filename"] == "sample.txt"
    assert result["size_bytes"] == len("Hello world")


def test_extract_metadata_file_not_found():
    with pytest.raises(FileNotFoundError):
        extract_metadata("/non/existent/file.txt")
