# HTTP API

HTTP-адаптер предназначен для локальных сервисов, которым нужен язык-независимый
доступ к чтению Time. В версии `0.3.0` у него нет маршрутов записи, скачивания
файлов и WebSocket proxy.

## Запуск

Установите extra `service` и создайте отдельный ключ:

```bash
uv sync --locked --no-editable --all-extras
uv run --no-sync timetk service-key create
uv run --no-sync timetk serve
```

При первом создании ключ печатается один раз и сохраняется в системном keyring.
Копию нужно положить в менеджер секретов вызывающего сервиса. Проверить наличие
без вывода значения:

```bash
uv run --no-sync timetk service-key status
```

Сервер по умолчанию слушает `http://127.0.0.1:8765`. Параметры:

```text
timetk serve [--host HOST] [--port PORT] [--allow-network]
```

Нелокальный `HOST` требует `--allow-network`. Этот флаг только снимает локальную
проверку: TLS, firewall, rate limit и проверку удалённых клиентов он не добавляет.

## Аутентификация

`GET /health` открыт. Все `/v1/...` требуют:

```http
Authorization: Bearer SERVICE_KEY
```

Сравнение ключа выполняется constant-time. Это отдельный ключ адаптера, не токен
Time. Он даёт доступ ко всем профилям, доступным процессу Time Toolkit, поэтому его
нельзя передавать клиенту с более узкими правами.

Для процесса без keyring задайте:

```text
TIME_TOOLKIT_SERVICE_API_KEY
```

Токены Time по-прежнему поступают через keyring или профильные переменные
`TIME_TOOLKIT_<PROFILE>_TOKEN`.

## Формат ответа

Успешный `/v1`-ответ:

```json
{
  "schema_version": "1.0",
  "profile": "university",
  "server": "https://time.cu.ru",
  "data": {},
  "meta": {"read_only": true}
}
```

Ошибка Time Toolkit:

```json
{
  "schema_version": "1.0",
  "error": "Channel not found: missing",
  "code": 5,
  "details": {}
}
```

HTTP status берётся из ошибки, если он известен: 401, 403, 404 или 409. Ошибки
параметров и конфигурации обычно возвращаются как 400. Отсутствующий или неверный
service key возвращает стандартный FastAPI-ответ 401 с полем `detail` и заголовком
`WWW-Authenticate: Bearer`. Ошибка валидации query возвращает 422.

Интерактивные Swagger/ReDoc и `/openapi.json` отключены, чтобы локальный runtime не
публиковал лишнюю поверхность. Этот файл — нормативный справочник маршрутов.

## Маршруты

Всего доступно 14 GET-маршрутов, включая health check.

### Состояние и профили

#### `GET /health`

Без аутентификации.

```json
{"status":"ok","version":"0.3.0","write_routes":false}
```

Health check подтверждает работу процесса, но не проверяет keyring, токены Time и
доступность инстансов.

#### `GET /v1/profiles`

Возвращает профили без секретов: `name`, `base_url`, `team_id`, `auth_method`,
`timezone`, `mcp_enabled`, `write_policy`, `allowed_websocket_hosts`, `default`.

### Пользователь и каналы

#### `GET /v1/{profile}/me`

Текущий пользователь профиля.

#### `GET /v1/{profile}/channels`

| Query | Тип | Default | Ограничение |
|---|---|---:|---|
| `pattern` | string | `""` | substring системного или отображаемого имени |
| `channel_type` | string | `""` | обычно `O`, `P`, `D` или `G` |
| `limit` | int | 100 | 1–200 |

### Непрочитанное и сообщения

#### `GET /v1/{profile}/unread`

| Query | Тип | Default |
|---|---|---:|
| `channel` | string | `""` |
| `include_posts` | bool | false |
| `mentions_only` | bool | false |
| `limit` | int 1–200 | 50 |

Без `channel` возвращает сводку команды, тредов и список каналов с ненулевыми
счётчиками. С `channel` — unread этого канала. `include_posts` читает сообщения
вокруг первой непрочитанной позиции, но не отмечает их прочитанными.

#### `GET /v1/{profile}/channel-posts`

Обязательный query `channel`.

| Query | Тип | Default |
|---|---|---:|
| `channel` | string | обязателен |
| `since` | string | `""` |
| `until` | string | `""` |
| `authors` | string, повторяемый | отсутствует |
| `contains` | string | `""` |
| `limit` | int 1–200 | 100 |

Пример нескольких авторов:

```text
?channel=general&authors=alex&authors=pat&since=7d
```

#### `GET /v1/{profile}/search`

| Query | Тип | Default |
|---|---|---:|
| `query` | string | `""` |
| `channels` | string, повторяемый | отсутствует |
| `authors` | string, повторяемый | отсутствует |
| `since` | string | `""` |
| `until` | string | `""` |
| `limit` | int 1–200 | 100 |

Пустой `query` допускается, например для фильтра по автору. Временные значения:
дата, ISO 8601 или `7d`/`24h`/`30m`.

#### `GET /v1/{profile}/thread`

Обязательный query `target`: post ID либо Time URL.

#### `GET /v1/{profile}/followed-threads`

| Query | Тип | Default |
|---|---|---:|
| `include_posts` | bool | false |
| `limit` | int 1–200 | 100 |

#### `GET /v1/{profile}/flagged`

Необязательный `channel` и `limit` 1–200, default 100.

#### `GET /v1/{profile}/pinned`

Обязательный query `channel`.

### Пользователи, реакции и файлы

#### `GET /v1/{profile}/user-activity`

Обязательный `username`; необязательные `since`, `until`, `limit` 1–200, default
200. Результат включает пользователя, количество сообщений и тредов, разбивку по
каналам и сами сообщения.

#### `GET /v1/{profile}/reactions`

Обязательный query `target`: post ID либо Time URL.

#### `GET /v1/{profile}/file-info`

Обязательный query `file_id`. Файл не скачивается.

## Примеры

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $TIME_TOOLKIT_SERVICE_API_KEY" \
  'http://127.0.0.1:8765/v1/university/unread?include_posts=true&limit=20'
```

```python
import os

import httpx

headers = {"Authorization": f"Bearer {os.environ['TIME_TOOLKIT_SERVICE_API_KEY']}"}
with httpx.Client(base_url="http://127.0.0.1:8765", headers=headers, timeout=45) as client:
    response = client.get(
        "/v1/university/search",
        params=[("query", "экзамен"), ("channels", "general"), ("since", "7d")],
    )
    response.raise_for_status()
    posts = response.json()["data"]
```

## Ротация ключа

```bash
timetk service-key create
```

Если ключ уже есть, команда покажет предупреждение и запросит подтверждение.
После ротации все клиенты со старым ключом сразу перестанут проходить проверку.
Перезапустите HTTP-процесс: он читает ключ один раз при создании приложения.

Удаление:

```bash
timetk service-key clear
```

Без ключа `timetk serve` не запускается.

## Развёртывание

Рекомендуемый вариант — один процесс на той же машине, связь через loopback,
service key в secret store потребителя. Если потребителей несколько и им нужны
разные права, поднимайте отдельные процессы Time Toolkit с разными config dir,
профилями и ключами.

Для изоляции задайте `TIME_TOOLKIT_CONFIG_DIR` каждому процессу. Keyring остаётся
общим для пользователя ОС; в контейнере лучше передавать только нужные профильные
токены через секреты окружения.
