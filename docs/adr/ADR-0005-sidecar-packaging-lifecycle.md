# ADR-0005: Упаковка sidecar (one-dir) и владение деревом процессов

- **Статус:** принято (2026-11-15)
- **Контекст:** PyInstaller one-file распаковывается при каждом старте и создаёт пару «бутлоадер → Python», из-за чего PID запущенного бинаря ≠ PID интерпретатора. `kill` по PID оставляет Python-процесс, что для продукта с доступом к медиа является инцидентом приватности (SEC-17). Tauri требует target-triple suffix в имени sidecar.

## Решение

1. **Только PyInstaller one-dir.** `-F`/one-file запрещён в релизной сборке; CI проверяет spec на отсутствие one-file.
2. Имя бинаря: `kadr-engine-<target-triple>[.exe]`; triple берётся из `rustc --print host-tuple` (fallback — платформенная эвристика). Скрипт `packaging/rename-sidecar.py` переименовывает артефакт до сборки установщика.
3. Engine запускается в новой process group (`setsid`/`start_new_session`), на Linux детям ставится `PR_SET_PDEATHSIG`; на Windows — Job Object с kill-on-close и `taskkill /T`; на macOS — process group + явный `killpg`.
4. Единый путь завершения `kadr.lifecycle.engine_shutdown()`:
   `POST /internal/shutdown` (bearer + CSRF) → wait ≤ 2 с → terminate group → wait ≤ 3 с → kill;
   затем удаление lock/port-файла.
5. Watchdog в sidecar завершает процесс при смерти родителя/закрытии stdin (проверка ≤ 5 с).
6. Single-instance lock не даёт второму экземпляру поднять второй Engine.
7. Токен и порт передаются через stdout-handshake, не через URL и не через логи.

## Последствия

- Холодный старт one-dir ≤ 2 с (NFR-16), размер в бюджете NFR-07; проверяется спайком C.
- Lifecycle-тесты (process group, отсутствие осиротевших процессов) входят в PR-гейт, а не только nightly.
- При отрицательном результате one-dir — Nuitka, а не возврат к one-file.
