# Нативная security-регрессия — Windows (2026-11-21)

Нативная (без Docker/seccomp) регрессия security-тестов на Windows.
Linux/macOS — пропущены (нет среды прогона); seccomp/Seatbelt не заявлены.

## Результат: 54 passed, 2 skipped, 0 failed

| Тест-файл | Покрытие |
|---|---|
| test_security_corpus.py | SEC-04/T04, SEC-06/T06 (path-traversal, PII) |
| test_injection_corpus.py | SEC-01 (prompt/pixel-инъекции) |
| test_malformed_corpus.py | malformed-корпус |
| test_resource_bomb.py | SEC-09/T09 (decompression/pixel/zip bomb) |
| test_zero_egress.py | SEC-12 (network=none + _deny_network) |
| test_job_object.py | SEC-17 (Job Object kill-on-close) |
| test_lifecycle_sandbox.py | sidecar lifecycle + graceful shutdown |
| test_api_security.py | API-auth (bearer + CSRF) |
| test_paths_security.py | path-санитизация |
| test_xmp_privacy.py | XMP-приватность |
| test_license_manifest.py | fail-closed license-weights гейт |
| test_fault_injection.py | fault-инъекции |

## SEC-02 VLM-ASR (typographic-инъекция) — 22%

Прогон 23 typographic-файлов через Qwen3-VL-2B (GPU):
- **ASR = 5/23 = 22%** — VLM выдал инъектированную метку (document/receipt) вместо реальной сцены.
- Распределение сцен: portrait 14, document 4, street 2, interior 2, receipt 1.
- Для сравнения: L1 (SigLIP2) ASR = 0% (видит только изображение, не текст).

**Вывод:** типографская атака целит в VLM (текст+изображение) — 22% пробития.
Это ожидаемо и документируется как известная уязвимость VLM-слоя (SEC-02);
защита — калибровка/TTA (SEC-03) + ансамбль CLIP↔VLM (FR-3.11).

## Пропущено (не Windows)
- seccomp/Seatbelt (Linux/macOS) — нет среды.
