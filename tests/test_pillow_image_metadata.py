import tempfile

from PIL import Image

from attack_surface.metadata import extract_metadata


def test_pillow_image_metadata(tmp_path):
    # Create a simple 10x10 red PNG image
    img_path = tmp_path / "red.png"
    img = Image.new("RGB", (10, 10), color="red")
    img.save(img_path, format="PNG")

    metadata = extract_metadata(str(img_path))

    # Basic sanity checks
    assert "format" in metadata
    assert metadata["format"] == "PNG"
    assert "size" in metadata
    assert metadata["size"] == (10, 10)
    assert "size_bytes" in metadata
    assert metadata["size_bytes"] > 0

if __name__ == "__main__":
    test_pillow_image_metadata(tempfile.mkdtemp())
