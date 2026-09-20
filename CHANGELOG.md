# История изменений

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии
следуют [Semantic Versioning](https://semver.org/lang/ru/).

## [Unreleased]

Пока нет изменений.

## [0.4.0] — 2026-09-20

### Добавлено

- MCP-инструмент `time_file_download` для скачивания одного вложения в
  `~/Downloads/Time Toolkit/` по явному относительному пути;
- документация по выбору имени файла через `time_file_info` и локальной
  границе загрузки.

### Безопасность

- MCP-загрузка не принимает абсолютные пути или `..`, не заменяет существующие
  файлы и помечена как локальная операция записи, а не read-only инструмент;
- обновлена зависимость `cryptography` до `50.0.0`.

## [0.3.0] — 2026-07-21

### Добавлено

- callback `on_connected` и модель `RealtimeConnection`, позволяющие выполнить
  REST catch-up до обработки событий нового WebSocket-соединения;
- флаг `timetk watch --lifecycle` для NDJSON-сигнала о первичном подключении и
  reconnect;
- непустое поле `meta` в NDJSON-envelope для типизации служебных строк потока;
- read-only модель `SidebarCategory`, Python API для sidebar categories и CLI-команды
  `categories`, `category-channels`.

### Изменено

- основной README переведён на английский, полная русская версия вынесена в
  `README.ru.md`;
- package metadata дополнена classifiers и ссылками на документацию, issues и
  исходный код.

### Исправлено

- неизвестные события в разных каналах больше не подавляются как дубли: их
  fallback `semantic_key` использует стабильный routing context и префикс `rt2:`;
- `resolve_channel()` и `iter_events()` закрытого `RealtimeService` одинаково
  возвращают понятную `UsageError`.

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

[Unreleased]: https://github.com/erstcl/time-toolkit/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/erstcl/time-toolkit/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/erstcl/time-toolkit/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/erstcl/time-toolkit/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/erstcl/time-toolkit/releases/tag/v0.1.0
