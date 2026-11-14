import io
from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient

from attack_surface.api import app
from attack_surface.metadata import extract_metadata
from PIL import Image

client = TestClient(app)


def create_image_bytes(size=(10, 10), color="blue", fmt="PNG"):
    buf = io.BytesIO()
    img = Image.new("RGB", size, color=color)
    img.save(buf, format=fmt)
    buf.seek(0)
    return buf.read()


def test_api_metadata_upload():
    img_bytes = create_image_bytes()
    files = {"file": ("test.png", img_bytes, "image/png")}
    resp = client.post("/metadata", files=files)
    assert resp.status_code == 200
    data = resp.json()
    # Validate some keys
    assert data["format"] == "PNG"
    assert tuple(data["size"]) == (10, 10)
    assert data["size_bytes"] > 0

# Ensure that API fails when file is missing or unsupported

def test_api_missing_file():
    resp = client.post("/metadata", files={})
    assert resp.status_code == 422  # FastAPI validation error


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
