# M2 / AI-lite — статус дочистки (2026-11-21)

Срез по задачам M2 из плана дочистки. Состояние — фактическое на дату.

## Сделано в этом раунде

| Задача | Результат |
|---|---|
| `tests/golden/eval_quality.py` | ✅ написан: «по дате», «по дате+времени», «по источнику»; precision@5 / nDCG@10 / CER — заглушки (нужны запросы + OCR-эталон). Порог гейта §13.2.1: «по дате» ≥ 0.98. |
| ANN recall@10 на 100K | ✅ поднят `ef_search` 256 → 512 → **recall 0.95** (p95 8.5 мс, NFR-18 ≥ 0.90 ✓). |
| FR-3.6 (сцены/ключевые кадры) | ✅ `kadr/video/scenes.py` (PySceneDetect ContentDetector + PyAV-извлечение кадров) + тест `test_video_scenes.py`. |
| Golden: метка `hard` | ✅ `hard=true` для 8 файлов (дата из filename) в `labels.json`. |

## Осталось / заблокировано

### SigLIP2 ONNX — запинен и вшит (2026-11-21)
✅ Скачаны int8 ONNX в `.models-test/siglip2/`, sha256 в `models/manifest.yaml`
(revision `ba1f3b08…`, `enabled=true`). `OnnxEmbeddingBackend` реализован:
tokenizers (EOS+pad 64) → `input_ids`; изображение resize 224×224 (bicubic) +
rescale 1/255 + normalize (x−0.5)/0.5 → `pixel_values`; `pooler_output` →
L2-нормализация (768-dim). Тест `test_onnx_embeddings.py` (3/3), полный сьют 152.
Включается через catalog-setting `embedding_backend=onnx` (иначе concept-hash).

### RapidOCR ONNX — запинен и вшит (in-process + sandbox) (2026-11-21)
✅ PP-OCRv4 det/rec + cls в `.models-test/rapidocr/`, sha256 в `models/manifest.yaml`
(`enabled=true`). Общий движок `kadr/ai/rapidocr.py`; `run_ocr` и sandbox-воркер
(`parser_worker._ocr`) пробуют RapidOCR первым, tesseract — fallback. Тест
`test_rapidocr.py` (2/2), полный сьют 154. Зависимость — extra `ocr`.

### FR-3.9 (лица) — заблокировано лицензией весов
InsightFace/буффало-модель — non-commercial. Нужен коммерчески лицензируемый
детектор+энкодер (например, SCRFD + AdaFace/ArcFace под допустимой лицензией)
или собственный лёгкий энкодер. Пока лица выключены.

### FR-3.10 (каскад L0→L4) / FR-3.11 (ансамбль+TTA) / FR-3.12 (персонализация)
Отложено: требуют активного SigLIP2/CLIP-энкодера (см. выше) и VLM-ансамбля.
SEC-03 (калибровка/TTA-consistency) тоже зависит от этого.

### Golden: adversarial ≥10% и «трудные» ≥40%
- adversarial: **0/357** — в `security/corpus` только текстовые payloads, изображений нет.
  Нужно сгенерировать ~36 состязательных файлов (malformed, pixel-attack, typographic,
  подменённый EXIF) и разметить.
- «трудные»: **8/357 (2.2%)** — нужно добавить ~135 файлов без EXIF / зернистых /
  неоднозначных сцен. Это задача сбора медиа (владелец) + переразметка VLM.

## Метрики гейта (обновятся после прогона)
- «по дате» ≥ 0.98 (§13.2.1) — ✅ **100% (319/319)**; «по дате+времени» 91.5%; «по источнику» считает с маппингом алиасов (exif→DateTimeOriginal и т.д.).
- ANN recall@10 ≥ 0.90 (NFR-18) — ✅ **0.95 @100K** (ef_search=512, p95 8.5 мс).
