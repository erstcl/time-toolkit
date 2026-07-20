# Time Toolkit

Time Toolkit — независимый клиент и набор адаптеров для Time Messenger и
Mattermost-compatible серверов. Один Python-пакет предоставляет команду `timetk`,
MCP-сервер `time-toolkit`, Python API, поток событий WebSocket и локальный
read-only HTTP API.

Проект не требует серверного плагина. Он работает с правами выбранного пользователя
или бота через публичный REST API v4 и не меняет состояние прочтения во время
обычных операций чтения.

## Возможности

- просмотр команд, каналов, личных диалогов, сообщений, тредов, непрочитанного,
  упоминаний, закреплений и флагов;
- поиск сообщений и пользователей, реакции, read receipts и метаданные файлов;
- отправка, ответы, редактирование, удаление, реакции, pin, flag, follow и явное
  изменение read state;
- загрузка и потоковое скачивание файлов;
- события Mattermost WebSocket с фильтрами, переподключением и удалением дублей;
- `text`, `json` и `ndjson` для людей, shell-скриптов и долгоживущих сервисов;
- MCP для Codex и других MCP-клиентов;
- импортируемый Python API и защищённый read-only HTTP API;
- любое количество изолированных профилей с отдельными серверами, токенами и
  политиками записи.

## Политика записи

Каждый профиль имеет один из трёх режимов:

| Режим | Интерактивный CLI и подтверждённый MCP | Автоматизация и `--yes` |
|---|---:|---:|
| `readonly` | запрещено | запрещено |
| `approval` | разрешено после подтверждения | запрещено |
| `fullauto` | разрешено | разрешено |

Новый профиль получает `approval`. MCP всегда использует двухшаговую запись с
одноразовым подтверждением, даже если профиль настроен как `fullauto`.

## Быстрый старт

Нужны Python 3.11+ и [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/erstcl/time-toolkit.git
cd time-toolkit
uv sync --locked --no-editable --all-extras

# Универсальный пример
uv run --no-sync timetk profile add example https://time.example.com

# Подробно поддерживаемый пример для Central University
uv run --no-sync timetk profile add university https://time.cu.ru
```

Сохраните токен через скрытый ввод. Значение попадёт в системное хранилище секретов,
а не в Git или `config.json`:

```bash
uv run --no-sync timetk -p university auth set
uv run --no-sync timetk -p university auth status --check
```

Первые команды чтения:

```bash
uv run --no-sync timetk -p university channels --pattern general
uv run --no-sync timetk -p university unread --with-posts
uv run --no-sync timetk -p university search "экзамен" --since 7d
```

Безопасный предпросмотр и интерактивная отправка:

```bash
uv run --no-sync timetk -p university post general -m "Привет" --dry-run
uv run --no-sync timetk -p university post general -m "Привет"
```

`--dry-run` не обращается к endpoint записи. Команда без `--yes` показывает точный
план и спрашивает `Proceed? [y/N]`. Для автоматизации оператор должен отдельно
назначить профилю `fullauto`.

## Какой интерфейс выбрать

| Задача | Интерфейс |
|---|---|
| Работа человека в терминале | CLI `timetk` |
| Codex или другой агент | MCP `time-toolkit` |
| Python-приложение в одном процессе | `TimeService` |
| Сервис на любом языке на той же машине | read-only HTTP API |
| Реакция на новые события | `timetk -o ndjson watch` |
| Короткий скрипт на любом языке | CLI с `-o json` или `-o ndjson` |

Для автоматической отправки из внешнего сервиса используйте отдельный профиль,
bot token, `fullauto` и собственный allowlist адресатов. HTTP API проекта намеренно
остаётся read-only.

## Документация

- [Установка, профили и получение токена](docs/getting-started.md)
- [Полный справочник CLI](docs/cli.md)
- [MCP для Codex и других клиентов](docs/mcp.md)
- [Интеграция с другими проектами](docs/integrations.md)
- [Python API](docs/python-api.md)
- [HTTP API](docs/http-api.md)
- [Модель безопасности](docs/security.md)
- [Архитектура и гарантии](docs/architecture.md)
- [Решение проблем](docs/troubleshooting.md)
- [Разработка и выпуск версий](docs/development.md)
- [История изменений](CHANGELOG.md)

## Границы проекта

Time Toolkit не входит в состав и не одобрен разработчиками Time Messenger,
Mattermost, Inc., Central University или операторами совместимых серверов. Названия
и товарные знаки принадлежат их владельцам.

В проекте нет входа по паролю, автоматического чтения профиля браузера, постоянного
зеркала сообщений, автоматического `mark-read`, HTTP-записи, полноэкранного TUI и
административных операций Mattermost. Причины описаны в
[архитектуре](docs/architecture.md#сознательные-ограничения).

Исходный код Time Toolkit написан независимо и взаимодействует с серверами только
через документированные сетевые интерфейсы. Лицензия проекта — [MIT](LICENSE).
