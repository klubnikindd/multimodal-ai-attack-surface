# Лицензии

## Код продукта

MIT — см. [`LICENSE`](LICENSE).

## Зависимости пользовательской сборки

| Компонент | Лицензия | Использование |
|---|---|---|
| Python | PSF-2.0 | runtime |
| PyYAML | MIT | правила YAML |
| Pillow | HPND | L0-метаданные/превью |
| blake3 (py) | CC0-1.0/Apache-2.0 | контент-хэши |
| FastAPI / Starlette / Pydantic | MIT/BSD | локальный API |
| sqlite-vec | MIT/Apache-2.0 | M0-векторный бэкенд (пинится) |
| usearch / hnswlib | Apache-2.0 | кандидаты ANN (P1) |
| exiftool | GPL/Artistic (Perl) | отдельный бинарь, sandbox; не линкуется |
| ffmpeg | LGPL-сборка | видео/аудио, динамическая линковка |
| tesseract | Apache-2.0 | OCR fallback |

## Веса моделей

Лицензии весов перечисляются **отдельно** от кода (D11). Манифест:
[`models/manifest.yaml`](models/manifest.yaml). В v0.1 все веса disabled до
M0-аудита; `license-weights` — fail-closed гейт.

Запрещены в пользовательской сборке: AGPL-код/веса, CC-BY-NC, research-only,
non-commercial, GPL-статическая линковка, веса с неизвестной лицензией.
InsightFace (`buffalo_l`) и ultralytics YOLO не используются.
