# Python API

Python API подходит, когда приложение работает на Python и может жить в том же
процессе. Он убирает subprocess и сериализацию JSON, но связывает приложение с
Python-моделями и синхронным API Time Toolkit.

## Подключение зависимости

До публикации пакета в PyPI для разработки рядом с репозиторием:

```bash
uv add --editable ../time-toolkit
```

Для воспроизводимой сборки из GitHub закрепите release tag:

```bash
uv add 'time-toolkit @ git+https://github.com/erstcl/time-toolkit.git@v0.2.0'
```

Не встраивайте GitHub token в URL зависимости. Для закрытого deployment можно
зеркалировать wheel во внутренний registry или собирать его в доверенном CI.

Нужные extras:

```bash
uv add 'time-toolkit[mcp,realtime,service] @ git+https://github.com/erstcl/time-toolkit.git@v0.2.0'
```

Основной Python API требует только базовые зависимости. Extras нужны, только если
ваш процесс запускает соответствующие адаптеры.

## Первый вызов

```python
from time_toolkit.service import TimeService

with TimeService.open("university") as time:
    me = time.me()
    posts = time.search_posts("экзамен", limit=20)
    unread = time.unread(with_posts=True, limit=50)
```

Контекстный менеджер закрывает внутренний `httpx.Client`. Если создать сервис без
`with`, вызовите `close()` в `finally`.

`TimeService.open()` читает профиль через `ConfigStore`, а токен — через
`SecretStore`. Сигнатура:

```python
TimeService.open(
    profile_name: str | None,
    *,
    config: ConfigStore | None = None,
    secrets: SecretStore | None = None,
    write_mode: Literal["automated", "confirmed"] = "automated",
) -> TimeService
```

`None` использует профиль по умолчанию. В приложениях указывайте профиль явно.

## Чтение

Методы синхронные:

```python
me() -> User
teams() -> list[Team]
team_id() -> str
doctor() -> dict[str, Any]

search_users(term, *, limit=20) -> list[User]
get_user(username) -> User
list_channels(*, pattern="", channel_type="", limit=100, max_pages=10) -> list[Channel]
list_dms(*, with_user="", limit=100) -> list[Channel]
resolve_channel(value) -> Channel

channel_posts(channel, *, since_ms=None, until_ms=None, authors=None,
              contains="", limit=100) -> list[Post]
search_posts(query, *, channels=None, authors=None, since_ms=None,
             until_ms=None, limit=100) -> list[Post]
thread(value) -> Thread
followed_threads(*, limit=100, with_posts=False) -> list[Thread]
unread(*, channel="", with_posts=False, mentions_only=False, limit=100) -> dict
flagged_posts(*, channel="", limit=100) -> list[Post]
pinned_posts(channel) -> list[Post]
user_activity(username, *, since_ms=None, until_ms=None, limit=200) -> dict

reactions(target) -> list[dict]
readers(targets) -> dict
file_info(file_id) -> dict
download_file(file_id, destination, *, overwrite=False) -> Path
```

`since_ms` и `until_ms` — Unix timestamp в миллисекундах. Для разбора пользовательских
строк доступен `time_toolkit.dates.parse_time_bound`, но это вспомогательный API;
интеграции могут использовать стандартный `datetime`.

Чтение не меняет read state. `download_file` меняет только локальную файловую
систему, пишет через временный файл и не изменяет Time.

## Realtime-события

Для долговременного WebSocket-consumer используйте публичный `RealtimeService`,
а не низкоуровневый `RealtimeClient` и не `service.client`:

```python
from time_toolkit.models import RealtimeConnection
from time_toolkit.realtime import RealtimeService


def catch_up(connection: RealtimeConnection) -> None:
    restore_missed_posts(overlap=True)

with RealtimeService.open("university") as realtime:
    channel = realtime.resolve_channel("general")
    for event in realtime.iter_events(
        event_types={"posted", "post_edited", "post_deleted"},
        channel_ids={channel.id},
        reconnect=True,
        max_reconnects=0,
        on_connected=catch_up,
    ):
        save_once("university", event.semantic_key, event)
```

`RealtimeService.open()` требует явное имя профиля, получает конфигурацию и секрет,
проверяет рекламируемый WebSocket host и закрывает HTTP/WebSocket-ресурсы при выходе
из `with`. Для изолированных deployment можно передать отдельные `ConfigStore` и
`SecretStore`:

```python
RealtimeService.open(
    profile_name: str,
    *,
    config: ConfigStore | None = None,
    secrets: SecretStore | None = None,
) -> RealtimeService
```

`iter_events()` поддерживает `event_types`, `channel_ids`, `reconnect`,
`max_reconnects` и `on_connected`. Ноль reconnect-попыток означает неограниченное
переподключение. Callback получает `RealtimeConnection` после успешной
аутентификации, но до первого обычного события соединения. На первом подключении
`reconnected=False`, на последующих — `True`. Выполняйте в callback REST catch-up;
если он завершится ошибкой, live-обработка не продолжится. `AuthenticationError`
завершает iterator немедленно; разрывы сети используют ограниченный exponential
backoff.

Каждый `RealtimeEvent` содержит исходные нормализованные `data` и `broadcast`,
`event`, `seq`, вычисленные `channel_id` и `post_id`, необязательный типизированный
`post` и непрозрачный версионированный `semantic_key`. Известные post/reaction
events используют `rt1:`, неизвестные события с routing context — `rt2:`. Не
проверяйте конкретный префикс и не разбирайте ключ. Он не зависит от WebSocket
`seq`, поэтому повтор события после reconnect получает то же значение. Ключ
server-local: постоянная база должна использовать пару `(profile, semantic_key)`.

WebSocket не является durable queue. После старта и каждого reconnect consumer
обязан выполнить REST catch-up с небольшим временным overlap, повторно отбросить
известные `semantic_key`/`post_id`/`update_at` и только затем продолжить live-поток.
`on_connected` обеспечивает эту границу для Python. Встроенная дедупликация хранит
только последние 2000 событий в памяти процесса.

Python-процесс с `RealtimeService` получает токен через `SecretStore`. Если consumer
не должен иметь доступ к токену, запускайте `timetk -o ndjson watch` отдельным
gateway-процессом и передавайте consumer только его stdout.

## Запись и `write_mode`

По умолчанию сервис работает как автоматическая интеграция:

```python
with TimeService.open("university") as time:
    post = time.create_post(
        "general",
        "Результат фоновой задачи",
        idempotency_key="job-1842-result",
    )
```

Этот вызов выполнится, только если профиль имеет `write_policy="fullauto"`.
`approval` и `readonly` вернут `time_toolkit.errors.PermissionError` до сетевого
запроса записи.

`write_mode="confirmed"` существует для доверенного UI или оркестратора, который
уже показал человеку точный план и получил отдельное согласие:

```python
with TimeService.open("example", write_mode="confirmed") as time:
    post = time.reply(root_post_id, approved_text, idempotency_key=operation_id)
```

`confirmed` работает для политик `approval` и `fullauto`, но блокируется
`readonly`. Само строковое значение не доказывает согласие и не является
механизмом аутентификации. Не используйте `confirmed` в cron, webhook handler или
фоновом worker. Для агента безопаснее готовый двухшаговый MCP-протокол.

Методы записи:

```python
create_post(target, message, *, file_ids=None, idempotency_key="") -> Post
reply(target, message, *, file_ids=None, idempotency_key="") -> Post
edit_post(target, message) -> Post
delete_post(target) -> dict
pin_post(target, *, pinned) -> dict
react(target, emoji, *, add) -> dict
flag_post(target, *, flagged) -> dict
follow_thread(target, *, following) -> dict
mark_unread(target) -> dict
mark_channel_read(channel) -> dict
upload_files(channel, files) -> list[str]
```

Все они проверяют режим до обращения к endpoint записи. Пустое значение
`idempotency_key` у `create_post` и `reply` заменяется случайным UUID. Для
воспроизводимой внешней операции передавайте свой ключ.

## Модели данных

Модели — frozen dataclasses со `slots`.

### `User`

`id`, `username`, `first_name`, `last_name`, `nickname`, `email`, `position` и
вычисляемое свойство `display_name`.

### `Team`

`id`, системное `name`, отображаемое `display_name`.

### `Channel`

`id`, `name`, `display_name`, `type`, `team_id`, `total_msg_count`, `label`.
`label` для direct message содержит понятное имя собеседника, когда оно доступно.

### `Post`

`id`, `channel_id`, `user_id`, `message`, `create_at`, `update_at`, `delete_at`,
`root_id`, `author`, `reply_count`, `is_pinned`, `is_mention`, `file_ids`,
`permalink`, `post_type`, `edit_at`, `is_from_bot` и свойство `create_at_iso`.
Timestamps хранятся в миллисекундах. Полный `props` не копируется в `Post`, но
остаётся в `RealtimeEvent.data`, если он пришёл в WebSocket payload.

### `RealtimeEvent`

`event`, `data`, `broadcast`, `seq`, `channel_id`, `post_id`, `post` и
`semantic_key`. Неизвестные поля `data` и `broadcast` сохраняются. Для
JSON-совместимого представления используйте тот же `primitive()`.

### `RealtimeConnection`

`state`, `reconnected` и `connection_id`. Модель передаётся в `on_connected` до
обычных событий нового соединения. `connection_id` может быть пустым, если сервер
подтвердил authentication challenge раньше события `hello`.

### `Thread`

`id`, `channel_id`, `channel_name`, `root_message`, `reply_count`,
`last_reply_at`, `unread_replies`, `unread_mentions`, `participant_ids`, `posts`,
`permalink`.

Для JSON-совместимого значения используйте:

```python
from time_toolkit.models import primitive

payload = primitive(posts)
```

## Ошибки

Все ожидаемые ошибки наследуются от `TimeToolkitError` и имеют `message`,
`exit_code`, необязательный `status_code` и `details`.

| Класс | Типичная причина |
|---|---|
| `UsageError` | неправильное значение или payload |
| `ConfigError` | повреждённая конфигурация или недоступный keyring |
| `AuthenticationError` | нет токена, 401, истёкшая сессия |
| `PermissionError` | 403 или локальная политика записи |
| `NotFoundError` | нет профиля, канала, пользователя или сообщения |
| `NetworkError` | DNS, timeout, TLS, разрыв соединения |
| `ConflictError` | неоднозначный канал или существующий файл |
| `ConfirmationRequired` | относится главным образом к CLI |

```python
from time_toolkit.errors import AuthenticationError, TimeToolkitError

try:
    with TimeService.open("university") as time:
        result = time.me()
except AuthenticationError:
    # Попросить оператора обновить токен, не печатая старый.
    raise
except TimeToolkitError as exc:
    logger.warning("Time operation failed: code=%s message=%s", exc.exit_code, exc.message)
```

Не логируйте `details` без проверки: при неоднозначности там есть названия
каналов, а будущие ошибки могут содержать другие рабочие метаданные.

## Синхронность и жизненный цикл

`TimeService` и его `TimeClient` синхронные и не заявлены как thread-safe. Создавайте
отдельный экземпляр на worker или запрос, не передавайте один сервис между
потоками.

В async-приложении выполняйте целую операцию в worker thread, причём создавайте и
закрывайте сервис внутри неё:

```python
import asyncio

from time_toolkit.service import TimeService


def load_unread():
    with TimeService.open("university") as time:
        return time.unread(limit=20)


result = await asyncio.to_thread(load_unread)
```

Для приложения на другом языке или отдельного процесса используйте
[HTTP API](http-api.md) либо [CLI/NDJSON](integrations.md).

## Совместимость версий

Проект следует SemVer, но версия `0.x` означает, что Python signatures ещё могут
меняться между minor-релизами. Закрепляйте tag или commit и читайте
[CHANGELOG](../CHANGELOG.md). Версия JSON envelope (`schema_version`) меняется
отдельно от версии пакета; проверяйте её при разборе данных.
