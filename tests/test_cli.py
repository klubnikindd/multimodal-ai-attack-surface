import json
import subprocess
import sys


def run_cli(file_path, args=None):
    """Helper to run the CLI as a subprocess.

    Args:
        file_path: Path to the file to pass to CLI.
        args: List of additional CLI arguments.
    Returns:
        CompletedProcess object from subprocess.
    """
    cmd = [sys.executable, "-m", "attack_surface.cli", file_path]
    if args:
        cmd[3:3] = args  # insert args after module spec
    return subprocess.run(cmd, capture_output=True, text=True)


def test_cli_success(tmp_path):
    # Create a temporary file with known content
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Hello world")

    result = run_cli(str(file_path))
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    # Parse JSON output
    data = json.loads(result.stdout)
    # Check expected keys
    assert data["filename"] == "sample.txt"
    assert data["size_bytes"] == len("Hello world")


def test_cli_file_not_found(tmp_path):
    fake_path = tmp_path / "nonexistent.txt"
    result = run_cli(str(fake_path))
    assert result.returncode != 0
    assert "File not found" in result.stderr
