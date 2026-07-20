# MCP для Codex и других клиентов

Команда `timetk mcp` запускает локальный сервер
[Model Context Protocol](https://modelcontextprotocol.io/specification/2025-06-18/server/tools/)
по STDIO. Он не открывает порт. Клиент запускает дочерний процесс и обменивается с
ним MCP-сообщениями через stdin/stdout.

Актуальные варианты подключения Codex описаны в
[официальном руководстве OpenAI](https://learn.chatgpt.com/docs/extend/mcp).

## Подключение

Для Codex:

```bash
codex mcp add time-toolkit -- \
  /absolute/path/to/time-toolkit/.venv/bin/timetk mcp
```

Эквивалентная конфигурация:

```toml
[mcp_servers.time-toolkit]
command = "/absolute/path/to/time-toolkit/.venv/bin/timetk"
args = ["mcp"]
default_tools_approval_mode = "writes"
required = true
```

Имя MCP-сервера — `time-toolkit`; команда терминала — `timetk`. Это разные
идентификаторы одной системы.

Проверка регистрации:

```bash
codex mcp list
```

В Codex TUI выполните `/mcp`. В IDE extension или ChatGPT desktop откройте
`Settings` → `MCP servers`, добавьте STDIO server с абсолютной командой
`.venv/bin/timetk` и аргументом `mcp`, затем перезапустите extension или desktop.
CLI, IDE extension и desktop на одном Codex host используют общую MCP-конфигурацию.

По умолчанию настройка хранится в `~/.codex/config.toml`. Для одного доверенного
проекта её можно положить в `.codex/config.toml` этого проекта. `required=true`
заставляет Codex явно сообщить об ошибке запуска сервера вместо продолжения без
Time-инструментов.

Другому MCP-клиенту передайте тот же `command`, `args` и окружение. Рабочий каталог
не важен: конфигурация и keyring общие для пользователя ОС.

## Общие правила

- каждый инструмент работы с Time требует явный `profile`, кроме локального
  `time_profiles`;
- профиль с `mcp_enabled=false` недоступен и для чтения, и для записи;
- чтение не меняет read state;
- сервер возвращает envelope версии `1.0`;
- ошибки Time Toolkit преобразуются в MCP `ToolError` без traceback и секретов;
- MCP-процесс наследует права, keyring и переменные окружения клиента.

## Инструменты чтения

| Инструмент | Обязательные параметры | Необязательные параметры |
|---|---|---|
| `time_profiles` | — | — |
| `time_me` | `profile` | — |
| `time_channels` | `profile` | `pattern`, `channel_type`, `limit=100` |
| `time_unread` | `profile` | `channel`, `include_posts=false`, `mentions_only=false`, `limit=50` |
| `time_posts` | `profile`, `channel` | `since`, `until`, `authors`, `contains`, `limit=100` |
| `time_search` | `profile`, `query` | `channels`, `authors`, `since`, `until`, `limit=100` |
| `time_thread` | `profile`, `target` | — |
| `time_followed_threads` | `profile` | `include_posts=false`, `limit=100` |
| `time_flagged` | `profile` | `channel`, `limit=100` |
| `time_pinned` | `profile`, `channel` | — |
| `time_user_activity` | `profile`, `username` | `since`, `until`, `limit=200` |
| `time_reactions` | `profile`, `target` | — |
| `time_readers` | `profile`, `targets` | — |
| `time_file_info` | `profile`, `file_id` | — |

`limit` у MCP должен быть от 1 до 200. `channel_type` принимает пустую строку,
`O`, `P`, `D` или `G`. Временные значения совпадают с CLI: ISO 8601, дата или
`7d`/`24h`/`30m`.

Все инструменты таблицы имеют MCP-аннотации `readOnlyHint=true`,
`destructiveHint=false`, `idempotentHint=true`, `openWorldHint=true`.

## Двухшаговая запись

Прямых инструментов `send`, `delete` или `mark_read` нет. Для любой записи клиент
сначала вызывает `time_prepare_write`, а после отдельного ответа человека —
`time_commit_write`.

### 1. Подготовка

```text
time_prepare_write(
  profile: str,
  action: str,
  target: str,
  message: str = "",
  emoji: str = "",
  file_ids: list[str] | null = null,
  file_paths: list[str] | null = null
)
```

Допустимые `action`:

```text
post, reply, edit, delete, pin, unpin, react, unreact,
flag, unflag, follow, unfollow, mark-unread, mark-read, upload-files
```

Проверки payload:

- `post` и `reply` требуют текст или хотя бы один `file_id`;
- `edit` требует непустой текст;
- `react` и `unreact` требуют `emoji`;
- `upload-files` требует `file_paths`;
- `file_paths` запрещены для остальных действий;
- `target` всегда обязателен и не может быть пустым.

Инструмент возвращает точный план:

```json
{
  "schema_version": "1.0",
  "profile": "example",
  "server": "https://time.example.com",
  "data": {
    "operation_id": "random-id",
    "profile": "example",
    "server": "https://time.example.com",
    "action": "post",
    "target": "general",
    "message": "Точный текст",
    "emoji": "",
    "file_ids": [],
    "file_paths": [],
    "created_at": 1784548800,
    "expires_in_seconds": 599,
    "confirmation": "CONFIRM random-id"
  },
  "meta": {"prepared": true}
}
```

`time_prepare_write` сам ничего не меняет, поэтому помечен `readOnlyHint=true` и
`destructiveHint=false`. При этом план может содержать чувствительный текст и пути
локальных файлов; MCP-клиент не должен писать его в общедоступный лог.

### 2. Согласие человека

Клиент показывает без сокращений:

- профиль и сервер;
- действие и адресата;
- полный текст, emoji, file IDs и локальные пути;
- фразу подтверждения.

Он ждёт новый, однозначный ответ. Первоначальная просьба «отправь сообщение» не
считается согласием на уже подготовленный план. Изменение текста, адресата или
профиля требует новой подготовки.

### 3. Выполнение

```text
time_commit_write(
  operation_id: str,
  confirmation: str
)
```

`confirmation` должна в точности равняться `CONFIRM <operation_id>`. Подготовка
действует 600 секунд, хранится только в памяти MCP-процесса и одноразовая. После
claim операция удаляется до сетевого запроса. Если сеть оборвалась, повторять тот
же `commit` нельзя: нужно проверить результат и подготовить новую операцию.

`time_commit_write` имеет `readOnlyHint=false`, `destructiveHint=true`,
`idempotentHint=false`, `openWorldHint=true`. Политика MCP-клиента должна
запрашивать системное разрешение для таких инструментов.

Подтверждённая MCP-запись разрешена для профилей с `approval` или `fullauto`.
`readonly` отклоняется уже на этапе `time_prepare_write`. Даже `fullauto` не
сокращает MCP flow: Codex и другие агенты всегда должны получить отдельное
согласие пользователя на конкретный preview.

## Пример поведения агента

Пользователь: «Ответь в проектном треде: буду к 15:00».

Корректная последовательность:

1. агент уточняет или разрешает профиль `example` и конкретный `target`;
2. вызывает `time_prepare_write(profile="example", action="reply", ...)`;
3. показывает полученный план целиком;
4. ждёт отдельное «да»;
5. передаёт точные `operation_id` и `confirmation` в `time_commit_write`;
6. сообщает permalink результата или ошибку.

Если пользователь меняет время на 16:00, агент не коммитит старый план, а создаёт
новый.

## Envelope и модели

Успешный ответ содержит:

```text
schema_version  "1.0"
profile         запрошенный профиль
server          фактический base URL
data            объект, список или scalar
meta            сведения адаптера
```

Пост включает `id`, `channel_id`, `user_id`, `message`, timestamps в миллисекундах,
`create_at_iso`, `root_id`, `author`, `reply_count`, flags, `file_ids` и
`permalink`. Подробные модели описаны в [Python API](python-api.md#модели-данных).

## Отключение профиля

```bash
timetk profile update example --disable-mcp
```

После этого инструменты вернут ошибку для `example`, но обычный CLI продолжит
работать. Включение обратно:

```bash
timetk profile update example --enable-mcp
```

Если MCP-клиент долго держит дочерний процесс, перезапустите сервер после обновления
пакета. Профильная конфигурация читается на каждый вызов, а код процесса — только
при старте.
