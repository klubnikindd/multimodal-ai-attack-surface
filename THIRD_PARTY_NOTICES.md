# Third-party notices

Этот файл дополняет `LICENSES.md` и обновляется вместе с lockfile/SBOM.

- **CPython** — PSF License.
- **Pillow** — HPND License.
- **PyYAML** — MIT.
- **blake3-py** — CC0-1.0 / Apache-2.0 (по выбору).
- **FastAPI, Starlette, Pydantic, Uvicorn** — MIT/BSD.
- **sqlite-vec** — MIT/Apache-2.0; версия пинится, см. ADR-0004.
- **exiftool** — Perl Artistic License / GPL; распространяется отдельным
  бинарём, исходники доступны у проекта exiftool, внутрь дистрибутива не
  линкуется.
- **FFmpeg** — LGPL-сборка (без GPL-компонентов), динамическая линковка.
- **Tesseract OCR** — Apache-2.0.
- **usearch / hnswlib** — Apache-2.0 (кандидаты ANN, P1).
- **Qwen3-VL GGUF (Qwen)** — Apache-2.0 (заявленная); конкретный чекпойнт
  проверяется на M0 и фиксируется в `models/manifest.yaml`; Thinking-сборки
  запрещены.
- **SigLIP 2 / OpenCLIP** — Apache-2.0 / MIT (заявленные); конкретные веса
  проверяются на M0.
- **RapidOCR / PaddleOCR** — Apache-2.0 (проверить веса).
- **Whisper / faster-whisper** — MIT (P1).

Полный SBOM (CycloneDX) формируется в CI (`sbom` job); при расхождении с этим
файлом приоритет у SBOM и `models/manifest.yaml`.
