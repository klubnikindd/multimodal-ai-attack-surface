# Synthetic security corpus

Корпус маленький, синтетический и версионируемый: без чужих фотографий и PII.
Версии файлов — JSON; каждый payload обязан детектироваться/отклоняться
соответствующим слоем (тест итерирует корпус, а не зашивает список на лету).

| Файл | Вектор | Проверка |
|---|---|---|
| `injection_payloads.json` | SEC-01/T01 (prompt injection) | `tests/kadr/test_injection_corpus.py` — ASR 0% |
| `path_traversal_payloads.json` | SEC-06/T06 (FS escape) | `tests/kadr/test_security_corpus.py` — 0 записей вне root |
| `pii_payloads.json` | SEC-04/T04 (PII) | `tests/kadr/test_security_corpus.py` — категории детектируются |

Полный бинарный корпус malformed-файлов добавляется на M3 с указанием лицензий.
