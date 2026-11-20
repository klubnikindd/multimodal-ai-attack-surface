# M0 spikes (blocking gates, §13.1 v3.1)

Статус на 2026-11-20 (обновляется по факту):

| Спайк | Статус | Что уже есть | Что осталось |
|---|---|---|---|
| **A. sqlite-vec/KNN** | ✅ на уровне адаптера | `scripts/sqlite_vec_smoke.py` зелёный на 3 ОС в CI; исправлена загрузка расширения (BLK-01, P0-01); регресс-тест в `tests/kadr/test_vector_index.py` | smoke внутри собранных пакетов (msi/dmg/AppImage/deb/rpm) — P1-03 после P1-05/P7-01 |
| **B. Sidecar-lifecycle** | ⚠️ M0-минимум зелёный | `scripts/check-sidecar-lifecycle.py` — 3 сценария (group / «внук» / single-instance); `pid_alive` через Win32 API (P0-02); тесты в `tests/kadr/test_lifecycle_sandbox.py` | сценарии §11.2.3 в реальном Tauri-хосте (P1-04) и полный набор M6 (P7-07) |
| **C. PyInstaller one-dir** | ✅ измерено на Windows (82,2 МБ, старт 18,5 с первый / 430 мс повторный, 0 orphans; 4 дефекта сборки исправлены) | `packaging/kadr-engine.spec` (one-dir, target-triple через `rename-sidecar.py`); первый прогон упал на `SPECPATH.parents[1]` — исправлено на `.parent` | замер холодного/повторного старта, размера, антивирусов — Ф1 |
| **D. VLM-бюджеты** | ⚠️ харнесс готов | `tests/spikes/vlm_bench.py` (llama.cpp, thinking off, ≤1024 px, 384 токена, честный `not_measured` без устройств) | прогон на 4 классах устройств × 2 разрешения (нужны машины/аренда GPU) |
| **E. Масштаб поиска** | ⚠️ харнесс готов, прогон идёт | `tests/scale/bench_scale.py` + smoke-тест; 10K/50K/100K измеряются | 200K/500K (длительные), решение по порогу в ADR-0004 |

Отрицательный результат спайка — решение по гейту §14, а не «продолжаем как есть».

Порядок работ: D → E → A → B → C (§13.1 v3.1).
