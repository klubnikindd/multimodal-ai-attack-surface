# M0 spikes (blocking gates)

- **A. sqlite-vec/KNN** — `scripts/sqlite_vec_smoke.py` (3 ОС, все форматы пакетов).
- **B. sidecar lifecycle** — `scripts/check-sidecar-lifecycle.py` + `tests/kadr/test_lifecycle_sandbox.py`.
- **C. PyInstaller one-dir** — `packaging/kadr-engine.spec` + `scripts/check-no-onefile.py`;
  холодный/повторный старт и размер замеряются на M0.
- **D. VLM-бюджеты** — таблица в `docs/BENCHMARKS.md`; требует устройств, пока не измерено.
- **E. масштаб поиска** — `tests/scale/README.md`; 50K/100K/200K/500K, p95 и recall@10.

Отрицательный результат спайка — решение по гейту §14 ТЗ, а не «продолжаем как есть».
