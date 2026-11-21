# M6 / Дистрибуция — статус (2026-11-21)

## Сделано (Windows)

| Пункт | Статус |
|---|---|
| **Установщик MSI** | ✅ `Kadr_0.1.0_x64_en-US.msi` (14.6 MiB) |
| **Установщик NSIS** | ✅ `Kadr_0.1.0_x64-setup.exe` (13.2 MiB) |
| **NFR-07 (≤60 МБ)** | ✅ 14.6/13.2 MiB |
| **Апдейтер (NFR-21)** | ⚠️ `tauri-plugin-updater` подключён + endpoints; **нужны**: `tauri signer generate` (подпись) + реальный хост манифеста |
| **Код-подпись** | ⏸ пропущен (нет сертификата) |

## Пропущено (нет нативных прогонов)
- macOS: dmg + подпись/нотаризация.
- Linux: AppImage/deb + репозиторий.
- CI 3-ОС: rust-build job есть, но нативный прогон только Windows.

## Команды
```powershell
pnpm tauri build   # -> src-tauri/target/release/bundle/{msi,nsis}
# апдейтер (после подписи):
#   tauri signer generate -w ~/.tauri/kadr.key   # приватный ключ
#   публичный ключ -> tauri.conf.json plugins.updater.pubkey
```
