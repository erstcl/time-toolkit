# Решение проблем

Начинайте с read-only диагностики. Она не выводит токен и не меняет состояние
сообщений:

```bash
uv run --no-sync timetk --version
uv run --no-sync timetk profile list
uv run --no-sync timetk -o json doctor --all --public
uv run --no-sync timetk -o json doctor --all
```

Сохраните exit code и stderr, но перед передачей лога проверьте, нет ли в нём
текстов сообщений, имён каналов и локальных путей.

## Команда не найдена

Если shell пишет `command not found: timetk`, запускайте через uv:

```bash
uv sync --locked --no-editable --all-extras
uv run --no-sync timetk --version
```

Либо используйте абсолютный путь:

```bash
/absolute/path/time-toolkit/.venv/bin/timetk --version
```

В активированной venv другого проекта uv может предупредить, что `VIRTUAL_ENV` не
совпадает. Это предупреждение, но для предсказуемости выйдите из чужого окружения
через `deactivate` или вызывайте бинарник по абсолютному пути.

## Глобальный параметр не распознан

`-p`, `-o` и `--debug` должны стоять перед названием команды.

```bash
# правильно
timetk -p university -o json unread

# неправильно
timetk unread -p university -o json
```

Параметры конкретной команды ставятся после неё.

## Нет профиля

Ошибки `No profile selected` или `Profile not found`:

```bash
timetk profile add university https://time.cu.ru
timetk profile list
timetk profile default university
```

Для записи default намеренно не помогает: передайте точный профиль через
`-p/--profile`.

## Keychain или keyring недоступен

На macOS разблокируйте login Keychain и убедитесь, что команда запущена в
пользовательской сессии, а не из sandbox без доступа. Повторите:

```bash
timetk -p university auth status
```

В headless Linux часто нет настроенного Secret Service. Передавайте runtime secret:

```text
TIME_TOOLKIT_UNIVERSITY_TOKEN
TIME_TOOLKIT_EXAMPLE_TOKEN
```

Не записывайте эти значения в tracked `.env`. Если нужен постоянный keyring,
настройте backend ОС и проверьте его отдельно библиотекой `keyring`.

## Нет токена

Ошибка показывает точную команду и имя env:

```bash
timetk -p university auth set
timetk -p university auth status --check
```

Токен вводится скрыто. Не передавайте его отдельным CLI-аргументом.

## 401: сессия отсутствует или истекла

Получите новый personal access token или `MMAUTHTOKEN`, затем перезапишите секрет:

```bash
timetk -p university auth set
timetk -p university auth status --check
```

Если Bearer-режим не принимается, попробуйте cookie-режим:

```bash
timetk -p university auth set --method cookie --csrf
```

Не переключайте режимы вслепую в production. Сначала проверьте один профиль через
`auth status --check`.

## 403: действие запрещено

Возможны два источника:

- Time не даёт аккаунту доступ к каналу, endpoint или чужому сообщению;
- локальная политика блокирует автоматическую запись.

Текст `Automated writes require the fullauto policy` означает, что вызов с
`--yes` или Python `write_mode="automated"` запрещён. Для ручной работы уберите
`--yes` и подтвердите preview. Для действительно автоматического сервиса оператор
может назначить:

```bash
timetk profile update PROFILE --write-policy fullauto
```

Текст `Writes are disabled` означает политику `readonly`. Она блокирует и ручной
CLI, и MCP. Если запись действительно нужна, оператор меняет её на `approval` или
`fullauto`.

Если это серверный 403, локальная настройка не поможет. Проверьте права аккаунта и
правильность профиля.

## Требуется подтверждение, код 8

В интерактивном терминале команда показывает план и ждёт `y`, `yes`, `д` или `да`.
Любой другой ответ отменяет операцию с кодом 8. В subprocess stdin обычно не TTY,
поэтому реальная автоматическая запись требует `--yes` и профильной policy.

Для проверки используйте `--dry-run`: он всегда заканчивается без сетевой записи.

## Канал не найден или неоднозначен

Сначала найдите точное имя:

```bash
timetk -p university channels --pattern часть-имени -o json
```

Помните о порядке глобальных аргументов; правильный вариант:

```bash
timetk -p university -o json channels --pattern часть-имени
```

При нескольких совпадениях ошибка code 7 содержит `details.matches`. Передайте
точное system name или 26-символьный channel ID. Не выбирайте первый вариант в
автоматическом скрипте.

## Сообщение или тред не найден

Проверьте, что ID относится к тому же профилю: идентификаторы разных инстансов не
взаимозаменяемы. Поддерживаются URL с `/thread/`, `/pl/` и `/posts/`.
Удалённый post либо post из недоступного канала может выглядеть как 404.

## Поиск пустой или неполный

Mattermost search зависит от серверной индексации и настроек форка. Проверьте:

- доступен ли канал текущему аккаунту;
- не слишком ли узкие `--since`, `--until`, `--author`, `--channel`;
- правильна ли timezone профиля;
- увеличен ли `--limit`;
- даёт ли `posts CHANNEL` результат за тот же период.

Дата-only `--until` включает весь день. ISO timestamp без offset трактуется в
таймзоне профиля, а не локальной таймзоне случайного контейнера.

## Network error, timeout или 429

`TimeClient` делает ограниченные повторы, затем возвращает code 6. Проверьте DNS,
VPN, proxy, TLS и доступность через:

```bash
timetk -p university doctor --public
```

Не запускайте бесконечный tight loop поверх CLI. Добавьте внешний backoff и общий
deadline. При неясном результате записи повторяйте только с тем же idempotency key
и сначала проверьте, не появилось ли сообщение.

## TLS certificate verify failed

Time Toolkit не отключает проверку сертификата. Обновите системный trust store,
установите корпоративный CA разрешённым способом или исправьте сертификат сервера.
Не меняйте URL на `http://`: для внешних hostname config отклоняет plain HTTP.

## WebSocket не подключается

Проверьте REST и token:

```bash
timetk -p university auth status --check
timetk -p university -o ndjson watch --once --no-reconnect
```

Типичные причины: VPN, proxy без WebSocket upgrade, отдельный WSS-домен, истёкшая
сессия или недоверенный CA. Клиент читает рекламируемый `WebsocketURL` из public
config; если запрос не удался, строит URL из base server.

Ошибка `WebSocket host ... is not trusted` означает, что сервер предложил передать
токен другому hostname или port. Сначала проверьте адрес у владельца инстанса,
затем разрешите точное значение:

```bash
timetk profile update PROFILE --websocket-host socket.example.com
```

Для нестандартного порта укажите `socket.example.com:8443`. Не разрешайте host,
который появился неожиданно или не принадлежит оператору вашего Time.

`--no-reconnect` полезен для быстрой диагностики. По умолчанию процесс может
выглядеть «зависшим», хотя он ждёт первое событие или переподключается. Включите
`--debug` и смотрите stderr, не stdout NDJSON.

## MCP-сервер не появился в клиенте

Проверьте абсолютный executable:

```bash
/absolute/path/time-toolkit/.venv/bin/timetk mcp
```

При ручном запуске процесс ждёт MCP stdin и ничего не печатает — это нормально.
Остановите его `Ctrl+C`. Проверьте `command`, `args=["mcp"]` и перезапустите
MCP-клиент. Не добавляйте `uv run`, если клиент запускается с непредсказуемым cwd;
абсолютный `.venv/bin/timetk` надёжнее.

Если профиль сообщает `MCP access is disabled`:

```bash
timetk profile update PROFILE --enable-mcp
```

## MCP-подтверждение не подходит

Причины `not found, expired, or already used`:

- прошло больше 600 секунд;
- MCP-процесс перезапустился;
- commit уже пытались выполнить;
- `operation_id` взят из другого процесса.

Подготовьте операцию заново и покажите новый план. Ошибка `does not match` означает,
что фраза не равна точному `CONFIRM <operation_id>`.

## HTTP API не запускается

Ошибка `No HTTP service key`:

```bash
timetk service-key create
timetk serve
```

`Address already in use` означает занятый порт; выберите другой через `--port`.
Для другого интерфейса нужен `--allow-network`, но сначала прочитайте раздел сети
в [модели безопасности](security.md#сеть).

HTTP 401 относится к service key, а не обязательно к Time token. Если
`/v1/profiles` работает, но `/v1/university/me` даёт 401, service key верен, а
профильный token Time — нет.

## Файл не скачивается или не загружается

Для download:

- проверьте file ID и доступ аккаунта к исходному сообщению;
- убедитесь, что каталог назначения доступен для записи;
- используйте `--overwrite` только если существующий файл можно заменить.

Для upload сначала сделайте `--dry-run`, проверьте точный channel и пути. Путь
читается процессом `timetk`; в MCP это машина, где запущен MCP-сервер, а не
удалённая машина пользователя.

## Минимальный отчёт об ошибке

Для локальной диагностики достаточно:

- версия `timetk --version`;
- OS и Python version;
- команда с заменёнными profile/channel/post ID;
- exit code;
- stderr без сообщений и персональных данных;
- прошёл ли `doctor --public` и `auth status --check`.

Никогда не прикладывайте token, cookie, CSRF, service key, реальный config целиком,
экспорт сообщений или скачанный файл.
