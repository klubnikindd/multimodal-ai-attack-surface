"""FastAPI application exposing metadata extraction functionality.

The app provides a single endpoint:

- **POST /metadata** – accepts a multipart file upload and returns the extracted
  metadata as JSON.

The endpoint uses the :func:`attack_surface.metadata.extract_metadata`
function internally. If the uploaded file cannot be processed, the API returns a
400 error.
"""

from pathlib import Path
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

from .metadata import extract_metadata

app = FastAPI(title="attack_surface Metadata API")


@app.post("/metadata", response_class=JSONResponse)
async def metadata_endpoint(file: UploadFile = File(...)) -> dict:
    """Extract metadata from an uploaded file.

    Parameters
    ----------
    file: UploadFile
        The uploaded media file.

    Returns
    -------
    dict
        JSON serialisable dictionary of extracted metadata.
    """
    # Save uploaded file to a temporary location
    try:
        tmp_suffix = Path(file.filename).suffix or ".tmp"
        with tempfile.NamedTemporaryFile(delete=False, suffix=tmp_suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)
        metadata = extract_metadata(str(tmp_path))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        # Clean up the temp file
        try:
            tmp_path.unlink()
        except Exception:
            pass
    return metadata


# Allow the app to be run directly for quick debugging
if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("attack_surface.api:app", host="0.0.0.0", port=8000, reload=True)
