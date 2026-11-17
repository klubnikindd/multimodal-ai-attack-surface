"""Metadata extraction utilities for media files."""

import os
from pathlib import Path

# Optional imports for image/video metadata extraction
try:
    from PIL import Image
except Exception:
    Image = None
try:
    import exifread
except Exception:
    exifread = None
try:
    import ffmpeg
except Exception:
    ffmpeg = None

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".gif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".flv"}


def _extract_image_metadata(file_path: str) -> dict:
    """Extract basic metadata from an image file.

    Returns a dictionary containing keys like ``format``, ``size`` (tuple), ``mode``, and
    optionally ``exif`` (dict of tag names to values). Missing libraries return an
    empty ``exif`` dict.
    """
    result: dict = {}
    if Image is None:
        # Pillow is not available; return empty image metadata
        return result
    try:
        with Image.open(file_path) as img:
            result["format"] = img.format
            result["size"] = img.size
            result["mode"] = img.mode
            # Extract EXIF using exifread if available
            if exifread is not None:
                with open(file_path, "rb") as f:
                    tags = exifread.process_file(f, details=False)
                # Convert exifread tags to a simple dict of str: str
                result["exif"] = {str(k): str(v) for k, v in tags.items()}
    except Exception:
        # If Pillow cannot open the file, ignore and return whatever was collected
        pass
    return result


def _extract_video_metadata(file_path: str) -> dict:
    """Extract basic metadata from a video file using ffmpeg.

    The function returns a dictionary with keys like ``duration`` (seconds), ``format``
    and ``streams`` (list of stream dictionaries). If ffmpeg or ffmpeg-python is not
    available, returns an empty dict.
    """
    result: dict = {}
    if ffmpeg is None:
        return result
    try:
        info = ffmpeg.probe(file_path)
        result["format"] = info.get("format", {}).get("format_name")
        result["duration"] = float(info.get("format", {}).get("duration", 0))
        result["streams"] = info.get("streams", [])
    except Exception:
        pass
    return result


def extract_metadata(file_path: str) -> dict:
    """Return a dictionary containing metadata for a given file.

    The metadata dictionary contains at least ``filename`` and ``size_bytes``. For
    image files the keys ``format``, ``size``, ``mode`` and optionally ``exif`` are
    added. For video files ``format``, ``duration`` and ``streams`` may be
    present. If the file type is unknown, only the generic keys are returned.

    Parameters
    ----------
    file_path: str
        Path to the file.

    Returns
    -------
    dict
        Dictionary containing the extracted metadata.

    Raises
    ------
    FileNotFoundError
        If the file does not exist.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")
    stat = os.stat(file_path)
    metadata: dict = {
        "filename": os.path.basename(file_path),
        "size_bytes": stat.st_size,
    }

    ext = Path(file_path).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        metadata.update(_extract_image_metadata(file_path))
    elif ext in VIDEO_EXTENSIONS:
        metadata.update(_extract_video_metadata(file_path))

    return metadata


__all__ = ["extract_metadata"]
