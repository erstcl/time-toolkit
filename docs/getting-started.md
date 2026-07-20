# Установка и первый запуск

Руководство доводит чистую установку до проверенного чтения и безопасного
предпросмотра отправки. Все примеры можно повторить с Central University Time или
другим Time/Mattermost-compatible сервером, к которому у вас есть разрешённый
доступ.

## Требования

- Python 3.11, 3.12, 3.13 или 3.14;
- [uv](https://docs.astral.sh/uv/getting-started/installation/);
- URL нужного сервера;
- собственный bot token, personal access token или действующий session token;
- системное хранилище секретов. На macOS используется Keychain.

CI проверяет Linux и все поддерживаемые версии Python. На Linux и Windows
библиотеке `keyring` может потребоваться системный backend. Для контейнеров и CI
предусмотрены переменные окружения.

## 1. Установка

Через GitHub CLI:

```bash
gh repo clone erstcl/time-toolkit
cd time-toolkit
uv sync --locked --no-editable --all-extras
```

Или через Git:

```bash
git clone https://github.com/erstcl/time-toolkit.git
cd time-toolkit
uv sync --locked --no-editable --all-extras
```

`--locked` запрещает незаметно менять зафиксированные зависимости.
`--no-editable` делает локальный запуск похожим на установленный пакет. После
синхронизации `--no-sync` ускоряет последующие команды и не меняет окружение.

```bash
uv run --no-sync timetk --version
uv run --no-sync timetk --help
```

## 2. Профили

Профиль объединяет имя, URL, Time team ID, таймзону, доступ MCP, политику записи и
разрешённые WebSocket-hosts. Секрета в профиле нет.

Универсальный пример:

```bash
uv run --no-sync timetk profile add example https://time.example.com
uv run --no-sync timetk profile show example
```

### Central University Time

Для учебного Time создайте профиль `university`:

```bash
uv run --no-sync timetk profile add university https://time.cu.ru \
  --write-policy approval
uv run --no-sync timetk profile default university
uv run --no-sync timetk -o json profile list
```

Первый созданный профиль автоматически становится профилем по умолчанию для
чтения. Любая запись всё равно требует явного `-p/--profile`, поэтому сообщение не
уйдёт в другой инстанс из-за default-настройки.

Для организационного или самостоятельно размещённого сервера шаги те же:

```bash
uv run --no-sync timetk profile add organization https://time.example.com \
  --write-policy approval
```

Замените URL на адрес, выданный владельцем сервера. Для внешнего hostname
принимается только HTTPS; обычный HTTP разрешён только для `localhost`,
`127.0.0.1` и `::1`.

Конфигурация находится:

- macOS: `~/Library/Application Support/time-toolkit/config.json`;
- Linux: `${XDG_CONFIG_HOME:-~/.config}/time-toolkit/config.json`;
- произвольный путь: переменная `TIME_TOOLKIT_CONFIG_DIR`.

Каталог создаётся с правами `0700`, файл — `0600`, обновление выполняется
атомарной заменой. URL с `user:password@host` отклоняются. Публичная проверка
сервера не требует токена:

```bash
uv run --no-sync timetk -p university doctor --public
uv run --no-sync timetk -o json doctor --all --public
```

## 3. Политика записи

При создании профиль получает `approval`.

| Значение | Что разрешено |
|---|---|
| `readonly` | только чтение; любая запись блокируется сервисным слоем |
| `approval` | интерактивный CLI и подтверждённая двухшаговая MCP-запись |
| `fullauto` | также неинтерактивный CLI `--yes` и Python `write_mode="automated"` |

Изменение режима:

```bash
timetk profile update university --write-policy readonly
timetk profile update university --write-policy approval
timetk profile update university --write-policy fullauto
```

`fullauto` не заменяет правила внешнего сервиса. Автоматическая интеграция должна
сама ограничивать допустимые профили, пользователей, каналы, виды операций и объём
сообщений. MCP для Codex всё равно требует отдельный prepare/approve/commit flow.

## 4. Токены

Используйте только собственные секреты и способы, разрешённые владельцем сервера.
Time и Mattermost обычно поддерживают:

1. bot token — лучший вариант для отдельного автоматического сервиса;
2. personal access token — постоянная интеграция от имени пользователя;
3. session token `MMAUTHTOKEN` — практический вариант для текущей пользовательской
   сессии, если первые два способа недоступны.

Официальная документация Time описывает
[bot token](https://docs.time-messenger.ru/api/cookbook/create-a-bot/),
[personal и session tokens](https://docs.time-messenger.ru/api/v4/%D0%B0%D1%83%D1%82%D0%B5%D0%BD%D1%82%D0%B8%D1%84%D0%B8%D0%BA%D0%B0%D1%86%D0%B8%D1%8F/)
и прямо допускает временное использование
[`MMAUTHTOKEN` из cookies браузера](https://docs.time-messenger.ru/api/cookbook/intro-integration/).
Session token может истечь или быть отозван и не рекомендуется как постоянный
production-секрет.

### Bot token или personal access token

Сохраните выданное значение через скрытый ввод:

```bash
uv run --no-sync timetk -p university auth set --method bearer
```

После приглашения `Time token:` вставьте значение и нажмите Enter. Терминал не
показывает введённые символы.

### `MMAUTHTOKEN` из собственной браузерной сессии

В Chromium-совместимом браузере, включая Comet:

1. Войдите в нужный Time и убедитесь, что открыт правильный домен.
2. Откройте Developer Tools.
3. Перейдите в `Application` → `Storage` → `Cookies`.
4. Выберите точный домен Time.
5. Найдите cookie `MMAUTHTOKEN` и скопируйте только её значение.
6. Не вставляйте значение в чат, issue, shell-команду, `.env`, скриншот или Git.
7. Сразу передайте его скрытому приглашению:

```bash
uv run --no-sync timetk -p university auth set --method bearer
```

Некоторые серверы принимают session token только как cookie. Тогда используйте:

```bash
uv run --no-sync timetk -p university auth set --method cookie
```

Если сервер отдельно требует CSRF token:

```bash
uv run --no-sync timetk -p university auth set --method cookie --csrf
```

`--csrf` открывает второе скрытое приглашение. Не включайте cookie-режим и CSRF
без необходимости.

Проверьте токен без его печати:

```bash
uv run --no-sync timetk -p university auth status
uv run --no-sync timetk -p university auth status --check
uv run --no-sync timetk -p university doctor
```

### Контейнеры и CI

Переменная окружения имеет приоритет над Keychain:

```text
TIME_TOOLKIT_UNIVERSITY_TOKEN
TIME_TOOLKIT_UNIVERSITY_CSRF
TIME_TOOLKIT_EXAMPLE_TOKEN
TIME_TOOLKIT_EXAMPLE_CSRF
```

Для произвольного профиля используется
`TIME_TOOLKIT_<PROFILE_IN_UPPERCASE>_TOKEN`; символы кроме букв и цифр заменяются
на `_`. Передавайте значения через secrets конкретной CI-системы или менеджер
секретов процесса. Не сохраняйте реальные значения в `.env` внутри проекта.

## 5. Первое чтение

Глобальные параметры ставятся перед командой:

```bash
uv run --no-sync timetk -p university -o json me
```

Проверьте основные операции:

```bash
uv run --no-sync timetk -p university teams
uv run --no-sync timetk -p university channels --limit 20
uv run --no-sync timetk -p university unread --limit 20
uv run --no-sync timetk -p university dms --limit 20
```

Эти вызовы не отмечают сообщения прочитанными. Даже `unread --with-posts` только
читает текущее серверное состояние. Read state меняют только явно названные
команды `mark-read` и `mark-unread`.

## 6. Проверка записи без записи

```bash
uv run --no-sync timetk -p university post general \
  -m "Проверка Time Toolkit" --dry-run
```

Результат содержит профиль, сервер, адресата и точный текст. Запрос записи в Time
не отправляется. Для реальной ручной отправки уберите `--dry-run`, проверьте
preview и ответьте на `Proceed? [y/N]`.

`--yes` считается автоматическим вызовом и работает только с `fullauto`:

```bash
uv run --no-sync timetk profile update university --write-policy fullauto
uv run --no-sync timetk -p university post general \
  -m "Сообщение интеграции" --yes --idempotency-key event-1842
```

Для человеческой работы и Codex оставляйте `approval`. Для внешней автоматизации
предпочтительнее создать отдельный bot profile с минимальными правами.

## 7. Отдельный WebSocket-host

Обычно `watch` подключается к тому же host, что REST API. Если сервер рекламирует
другой host, Time Toolkit блокирует передачу токена, пока адрес не разрешён явно:

```bash
timetk profile update example --websocket-host socket.example.com
timetk -p example -o ndjson watch --once --no-reconnect
```

Сначала подтвердите адрес у владельца сервера. Для нестандартного порта укажите
его явно, например `socket.example.com:8443`. Повторный `--websocket-host`
разрешает несколько адресов; `--clear-websocket-hosts` удаляет allowlist.

## 8. Подключение Codex

После установки исполняемый файл находится в `.venv/bin/timetk`:

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

Проверьте регистрацию через `codex mcp list`, затем перезапустите клиент. В Codex
TUI команда `/mcp` показывает активные инструменты. В IDE extension тот же сервер
можно добавить через `Settings` → `MCP servers` как STDIO server. MCP никогда не
отправляет сообщение одним вызовом: сначала создаётся точный preview, затем
человек подтверждает его, и только потом выполняется одноразовая операция.

## Локальная памятка

Реальные URL и инструкции для конкретной машины не обязаны находиться в
репозитории. Храните их, например, в
`~/.config/time-toolkit/SETUP.md`, а токены — только в Keychain. Каталог `.private/`
в рабочей копии также игнорируется Git, но внешний конфигурационный каталог
надёжнее переживает пересоздание репозитория.

## Дальше

- [CLI](cli.md) — все команды, форматы, даты и коды завершения;
- [интеграции](integrations.md) — выбор интерфейса и готовые шаблоны;
- [MCP](mcp.md) — инструменты и двухшаговая запись;
- [безопасность](security.md) — границы доверия, токены и WebSocket;
- [решение проблем](troubleshooting.md) — Keychain, 401/403, WebSocket и MCP.
