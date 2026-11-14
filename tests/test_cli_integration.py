import json
import subprocess
import sys
import os
import tempfile
from pathlib import Path
from PIL import Image


def _create_temp_image() -> str:
    """Create a temporary PNG image and return its file path."""
    img = Image.new("RGB", (10, 10), color="blue")
    temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(temp.name, format="PNG")
    temp.close()
    return temp.name


def _run_cli(args):
    """Run the CLI module as a subprocess and return subprocess.CompletedProcess."""
    cmd = [sys.executable, "-m", "attack_surface.cli"] + args
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result


def test_cli_outputs_correct_json():
    file_path = _create_temp_image()
    try:
        result = _run_cli([file_path])
        assert result.returncode == 0, f"CLI exited with {result.returncode}: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["filename"] == Path(file_path).name
        assert data["size"] == [10, 10]
        assert data["format"] == "PNG"
        assert data["size_bytes"] > 0
    finally:
        os.remove(file_path)


def test_cli_pretty_flag():
    file_path = _create_temp_image()
    try:
        result = _run_cli([file_path, "--pretty"])
        assert result.returncode == 0, f"CLI exited with {result.returncode}: {result.stderr}"
        # Pretty printed output should contain newline(s)
        assert "\n" in result.stdout
        data = json.loads(result.stdout)
        assert data["format"] == "PNG"
    finally:
        os.remove(file_path)


def test_cli_missing_file():
    result = _run_cli(["nonexistent_file_12345.png"])
    # CLI should exit with non-zero code and print error to stderr
    assert result.returncode != 0
    assert "File not found" in result.stderr
