# Security tests (MIAS)

Правило: **продуктовые** security-метрики (ASR инъекций, sandbox escape, zero-egress)
считаются только по нативным прогонам на Windows/macOS/Linux. Docker-прогон —
дополнительный и помечается `research-only`; он не заменяет нативный.

Каждая найденная уязвимость превращается в pytest-регрессию, которая остаётся в CI.
Текущие быстрые регрессии живут в `tests/kadr/test_paths_security.py`,
`test_api_security.py`, `test_metadata_and_dates.py`, `test_vlm_contract.py`,
`test_security_corpus.py`, `test_injection_corpus.py` (итерируют `security/corpus/`).
parser-worker использует POSIX rlimits/timeouts; seccomp/Seatbelt/Job Object и
egress enforcement — M3 и в v0.1 не заявляются. Полный корпус T01–T13 и fuzz — M3.
