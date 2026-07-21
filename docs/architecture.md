# Архитектура и гарантии

Time Toolkit отделяет Mattermost-протокол от пользовательских интерфейсов. CLI,
MCP и HTTP не реализуют собственные версии поиска, разрешения каналов или записи:
они вызывают общий `TimeService`.

## Слои

```text
CLI (cli.py)        MCP (mcp_server.py)        HTTP (http_api.py)
       │                    │                         │
       └────────────────────┼─────────────────────────┘
                            ▼
                    TimeService (service.py)
          разрешение целей, фильтры, модели, write policy
                            │
                            ▼
                     TimeClient (client.py)
             Mattermost REST API v4, retries, errors
                            │
          ┌─────────────────┴──────────────────┐
          ▼                                    ▼
ConfigStore + SecretStore              RealtimeService
config.py + auth.py                    RealtimeClient, WebSocket
```

Модели находятся в `models.py`, разбор времени — в `dates.py`, машинный вывод — в
`output.py`, единая иерархия ошибок — в `errors.py`.

## Зависимости от Mattermost

Time — форк Mattermost. Клиент использует
[REST API v4](https://developers.mattermost.com/integrate/reference/rest-api/) и
[WebSocket API](https://developers.mattermost.com/api-documentation/). Серверный
форк может изменить доступность endpoint, поля ответа, поиск или permissions.

Time Toolkit не требует server plugin и работает с правами обычного пользователя.
Он не пытается эмулировать API, которого нет у конкретного инстанса: ответ 404/403
передаётся как типизированная ошибка.

Основные группы upstream-запросов:

- system ping и client config;
- текущий пользователь, users, teams и channels;
- channel membership и unread counts;
- posts, search, threads, flags, pins, reactions и readers;
- create/update/delete post, follow, view/unread и upload;
- file metadata и потоковое скачивание;
- `/api/v4/websocket` или рекламируемый сервером WebSocket URL.

Некоторые read-only операции Mattermost, например search и batch lookup, сами
используют HTTP POST. Гарантия «чтение не меняет read state» основана на семантике
endpoint, а не только на HTTP-методе.

## Профили и конфигурация

Формат config version 2:

```json
{
  "version": 2,
  "default_profile": "university",
  "profiles": {
    "university": {
      "base_url": "https://time.cu.ru",
      "team_id": "",
      "auth_method": "bearer",
      "timezone": "Europe/Moscow",
      "mcp_enabled": true,
      "write_policy": "approval",
      "allowed_websocket_hosts": []
    }
  }
}
```

Отсутствующие необязательные поля получают безопасные default. Config version 1
читается без отдельной команды миграции: старое
`automated_writes_enabled=false` становится `approval`, а `true` — `fullauto`.
При следующем изменении профиля он сохраняется в новом формате.

Пути:

- macOS: `~/Library/Application Support/time-toolkit/config.json`;
- Linux: `$XDG_CONFIG_HOME/time-toolkit/config.json` или
  `~/.config/time-toolkit/config.json`;
- любой OS: каталог из `TIME_TOOLKIT_CONFIG_DIR`.

Имя профиля начинается с латинской буквы, имеет длину до 32 символов и содержит
только lowercase letters, digits, `_` и `-`. Вход нормализуется в lowercase.
Внешние URL обязаны использовать HTTPS; plain HTTP разрешён только для loopback.
URL с userinfo (`user:password@host`), path, query или fragment отклоняется.

Config записывается во временный файл в том же каталоге, `fsync`-ится и заменяется
через `os.replace`. Межпроцессной блокировки нет. Два одновременных процесса,
которые меняют профиль, могут перезаписать изменения друг друга; конфигурацию
следует менять как operator action, а не в параллельных worker.

## Секреты

Токен, CSRF и HTTP service key не сериализуются в config. `SecretStore` сначала
читает переменную окружения, затем keyring. Это позволяет одной установке работать
интерактивно на macOS и в headless deployment без отдельного формата секретов.

Bearer-режим отправляет `Authorization: Bearer TOKEN`. Cookie-режим отправляет
`Cookie: MMAUTHTOKEN=TOKEN`, `X-Requested-With: XMLHttpRequest` и при наличии
`X-CSRF-Token`. Для WebSocket Bearer token также добавляется как `MMAUTHTOKEN`
cookie и в authentication challenge, поскольку разные Mattermost-совместимые
серверы проверяют разные части handshake.

## Разрешение каналов

`resolve_channel` применяет порядок:

1. 26-символьный ID → точный lookup;
2. точное системное имя через team endpoint;
3. поиск substring среди видимых каналов;
4. одно совпадение принимается, несколько дают `ConflictError`, ни одного —
   `NotFoundError`.

Ведущий `~` удаляется. Для direct message человекочитаемый `label` строится из
username собеседника. Кэш пользователей и каналов живёт только внутри одного
`TimeService`.

## Чтение и пагинация

`channel_posts` использует серверную границу `since` либо страницы по 100,
применяет точные локальные фильтры времени, автора и substring текста, сортирует
новые первыми и обрезает до `limit`.

`search_posts` добавляет поддерживаемые Mattermost search terms `from:`, `after:` и
`before:`, объединяет результаты по post ID, затем повторно применяет локальные
фильтры. Дата в серверном query имеет точность дня, поэтому точная миллисекундная
граница всё равно проверяется локально.

`unread` вычисляет разницу `total_msg_count - membership.msg_count` и получает
сообщения вокруг unread position только по запросу. Ни один read flow не вызывает
`view channel` или `set post unread`.

Память не является зеркалом сервера: после завершения команды кэш пропадает.
Постоянной базы, поискового индекса и фоновой синхронизации нет.

## Модели и сериализация

Upstream JSON нормализуется в `User`, `Team`, `Channel`, `Post` и `Thread`.
Неизвестные поля Mattermost отбрасываются. Это стабилизирует результат между
адаптерами, но не сохраняет весь сырой ответ.

CLI JSON, HTTP и MCP используют envelope `schema_version=1.0`. Версия envelope —
контракт данных, независимый от package version. Text output удобен для человека,
но может меняться без новой schema version.

## Сетевые повторы

`TimeClient` имеет timeout 30 секунд и максимум три попытки по умолчанию.
Автоматически повторяются:

- `GET`, `HEAD`, `OPTIONS` при network error;
- любой запрос с idempotency key;
- ответы 429, 502, 503 и 504, если запрос можно повторить.

Backoff network errors: 0.5, 1 секунда между тремя попытками, максимум 4 секунды
при другом числе попыток. `Retry-After` читается как число секунд и ограничивается
30 секундами; без корректного заголовка используется экспоненциальная задержка.

Mattermost read endpoints на POST без idempotency key выполняются один раз.
Операции создания и изменения передают idempotency key; часть методов генерирует
UUID на уровне сервиса.

## Поток файлов

Upload идёт multipart-запросом с локального пути. Download не загружает весь файл
в память: `httpx.stream` пишет chunks во временный файл. Сервис заменяет назначение
через `os.replace`, поэтому неполный download не должен оставлять файл под
запрошенным именем.

Атомарная замена гарантируется только в пределах одной файловой системы и не
защищает от двух процессов, пишущих в один путь.

## WebSocket

Публичный `RealtimeService` требует явное имя профиля, загружает его через
`ConfigStore`, получает токен через `SecretStore` и управляет HTTP/WebSocket
lifecycle. Низкоуровневый `RealtimeClient` получает готовый `AuthContext` и
endpoint; внешним интеграциям он не нужен.

Сервис сначала запрашивает публичный `WebsocketURL`. Если это не удалось, он строит
`wss://HOST/api/v4/websocket` из base URL. Рекламируемый URL проверяется:

- схема только `ws`/`wss`, есть hostname, нет userinfo, query и fragment;
- HTTPS-профиль нельзя понизить до `ws://`;
- тот же hostname и эффективный port принимаются автоматически;
- другой hostname или port должен точно совпасть с `allowed_websocket_hosts`.

Allowlist хранит только hostname с необязательным port, без схемы, path и секрета.
Так ошибка или компрометация основного сервера не заставит штатный клиент молча
передать Authorization header, cookie и authentication challenge произвольному host.

Handshake:

1. TLS с системной проверкой сертификата;
2. auth headers/cookie;
3. Mattermost `authentication_challenge`;
4. ожидание подтверждения или `hello`;
5. нормализация вложенных `post`, `reaction`, `preference`;
6. создание `RealtimeEvent` и типизированного `Post`;
7. вычисление server-local `semantic_key` без transport `seq`;
8. фильтр типа и channel ID;
9. bounded-дедупликация последних 2000 semantic keys.

Для известных post/reaction events ключ использует upstream ID и timestamp ревизии;
для неизвестных — канонический hash типа и `data`. Префикс `rt1:` версионирует
алгоритм. Внешний consumer хранит пару `(profile, semantic_key)`, потому что профиль
и сервер намеренно не входят в ключ события.

При разрыве reconnect delay растёт 1, 2, 4, 8 секунд до 30. `AuthenticationError`
не повторяется. Дедупликация не сохраняется между процессами: WebSocket не является
durable queue, поэтому после перезапуска нужен REST catch-up с overlap.

## Политика записи

Запись проверяется в `TimeService` перед endpoint. У сервиса два режима:

```text
automated  вызов без отдельного человеческого подтверждения
confirmed  доверенный вызывающий код уже получил подтверждение
```

Поведение определяется `Profile.write_policy`:

| Policy | `confirmed` | `automated` |
|---|---:|---:|
| `readonly` | запрещён | запрещён |
| `approval` | разрешён | запрещён |
| `fullauto` | разрешён | разрешён |

CLI выбирает `automated` только для `--yes`; интерактивный flow использует
`confirmed`. MCP использует `confirmed` только после одноразового claim точной
фразы, в том числе для `fullauto`. HTTP не вызывает методы записи.

Проверка не является security boundary против произвольного локального Python-кода:
он способен запросить `confirmed`. Это защита от неверно подключённой штатной
автоматизации.

## Сознательные ограничения

- Нет логина по паролю: клиенту не нужен пароль пользователя.
- Нет автоматического чтения browser profile: доступ к одному токену не должен
  превращаться в доступ ко всем cookies браузера.
- Нет сохранения реальных сообщений, постоянного зеркала и индекса: это уменьшает
  объём чувствительных данных и код синхронизации.
- Нет автоматического `mark-read`: чтение из агента или сервиса не должно менять
  пользовательское состояние.
- Нет HTTP-записи: service key пока даёт широкий доступ ко всем профилям и не
  выражает human approval.
- Нет встроенного allowlist адресатов: эти правила принадлежат внешней интеграции,
  которая знает своих пользователей, каналы и бизнес-контекст.
- Нет собственного AI summarizer: агент или внешний сервис получает данные через
  стандартный интерфейс и применяет свою модель и policy.
- Нет TUI: он не добавляет интеграционных возможностей и заметно увеличивает
  поверхность поддержки.
- Нет Mattermost admin API, server plugin и управления пользователями/ролями.
- Нет multi-account session внутри одного профиля: отдельная учётная запись требует
  отдельного имени профиля.

## Публичные и внутренние поверхности

Для внешних проектов предназначены:

- команда `timetk`, её JSON envelope и exit codes;
- перечисленные MCP tools;
- перечисленные HTTP routes;
- `TimeService`, `RealtimeService` и модели из `time_toolkit.models`.

`TimeClient`, private helpers с `_`, внутренние Mattermost payload и структура
MCP prepared-write store могут меняться в `0.x`. Закрепляйте версию и проверяйте
changelog даже при использовании публичной поверхности.
