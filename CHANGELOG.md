# История изменений

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии
следуют [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

Пока нет изменений.

## [0.2.0] — 2026-07-21

### Добавлено

- публичные `RealtimeService` и `RealtimeEvent` для профильного Python-consumer;
- server-local `semantic_key` с версионированным форматом `rt1:` для дедупликации
  логических событий после reconnect;
- поля `post_type`, `edit_at` и `is_from_bot` в модели `Post`.

### Изменено

- `timetk watch` использует публичный realtime API и аддитивно возвращает
  `channel_id`, `post_id`, типизированный `post` и `semantic_key`;
- WebSocket-дедупликация больше не зависит от transport `seq`.

## [0.1.0] — 2026-07-20

### Добавлено

- произвольное число профилей с независимыми серверами, токенами и настройками;
- универсальные политики записи `readonly`, `approval` и `fullauto`;
- чтение сообщений, каналов, личных диалогов, тредов, unread, упоминаний,
  закреплений, флагов, реакций, read receipts, пользователей и файлов;
- операции записи с предпросмотром, подтверждением и idempotency key;
- CLI `timetk` с форматами `text`, `json` и `ndjson`;
- 16 MCP-инструментов и одноразовый двухшаговый протокол записи;
- Python API на основе `TimeService`;
- read-only HTTP API с Bearer-аутентификацией;
- Mattermost WebSocket с фильтрами, переподключением и дедупликацией;
- явный allowlist для WebSocket-host, отличающегося от основного сервера;
- Keychain/keyring и переменные окружения для секретов;
- автоматическая проверка Python 3.11–3.14;
- русская документация по всем публичным интерфейсам и интеграциям.

### Безопасность

- новый профиль получает безопасную политику `approval`;
- `readonly` блокирует все записи, `approval` требует подтверждение, а `fullauto`
  включается только явно;
- HTTPS нельзя понизить до незащищённого WebSocket, а токен не передаётся на
  неразрешённый host;
- URL профиля с `user:password@host` отклоняется;
- чтение не меняет read state;
- HTTP API не содержит маршрутов записи.

[Unreleased]: https://github.com/erstcl/time-toolkit/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/erstcl/time-toolkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erstcl/time-toolkit/releases/tag/v0.1.0
