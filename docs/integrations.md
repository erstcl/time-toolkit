# Интеграция с другими проектами

Time Toolkit не привязан к Codex. Его можно подключить к обычному backend,
локальному desktop-приложению, cron-задаче, агенту, ETL-процессу или сервису на
любом языке. Выбор интерфейса зависит от процесса и требований к записи.

## Выбор интерфейса

| Условие | Интерфейс | Почему |
|---|---|---|
| Python-приложение, общий процесс и окружение | Python API | типизированные модели, нет subprocess |
| Любой язык, короткие редкие операции | CLI `-o json` | минимум инфраструктуры |
| Долгоживущий consumer событий | `-o ndjson watch` | поток без polling |
| Отдельный локальный сервис, только чтение | HTTP API | обычный Bearer HTTP контракт |
| AI-клиент с участием человека | MCP | описанные tools и двухшаговая запись |
| Удалённый сервис | отдельный gateway вокруг одного из вариантов | Time Toolkit сам не является публичным API gateway |

Не запускайте HTTP-сервер ради одного Python-вызова и не парсите человекочитаемый
`text`. Для машинного обмена используйте `json`, `ndjson`, HTTP JSON или модели.

## Общий поток

```text
Внешний проект
  │
  ├─ CLI / NDJSON ─┐
  ├─ Python API ───┼─> TimeService ─> TimeClient ─> Time REST API v4
  ├─ HTTP read ────┤                         └──────> Time WebSocket
  └─ MCP ──────────┘

Профильная конфигурация ─> URL, timezone, team, policies
Secret store ────────────> token / CSRF
```

Все адаптеры используют один сервисный слой: одинаковое разрешение каналов,
пагинацию, даты, ошибки и модели. HTTP намеренно предоставляет только часть
операций чтения; WebSocket и запись через него недоступны.

## Sidebar categories как конфигурация интеграции

Если внешний проект группирует каналы так же, как пользователь в Time, читайте
папки через публичный API, а не через `TimeClient.request()`:

```python
from time_toolkit.service import TimeService

with TimeService.open("example") as time:
    categories = time.sidebar_categories()
    selected = time.resolve_sidebar_category("Study")
    channels = time.category_channels(selected.id)
```

Для другого языка используйте CLI:

```bash
timetk -p example -o json categories
timetk -p example -o json category-channels "Study"
```

ID категории предпочтительнее отображаемого имени для сохранённой конфигурации.
Имя разрешается только по точному совпадению. Порядок категорий и каналов приходит
от сервера; недоступные channel IDs пропускаются. Toolkit не изменяет sidebar и не
использует batch lookup каналов.

## Контракт JSON CLI

Для одиночного вызова запускайте команду с абсолютным путём и проверяйте три вещи:
timeout, exit code и `schema_version`.

```python
import json
import subprocess
from pathlib import Path

TIMETK = Path("/opt/time-toolkit/.venv/bin/timetk")


def time_json(profile: str, *command: str):
    completed = subprocess.run(
        [str(TIMETK), "--profile", profile, "--format", "json", *command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if completed.returncode != 0:
        try:
            error = json.loads(completed.stderr)
        except json.JSONDecodeError:
            raise RuntimeError(f"timetk failed with exit {completed.returncode}")
        raise RuntimeError(f"Time error {error['code']}: {error['error']}")

    payload = json.loads(completed.stdout)
    if payload.get("schema_version") != "1.0":
        raise RuntimeError("Unsupported Time Toolkit schema")
    if payload.get("profile") != profile:
        raise RuntimeError("Time Toolkit returned another profile")
    return payload["data"]


posts = time_json("university", "search", "экзамен", "--since", "7d", "--limit", "20")
```

`stdin=DEVNULL` не даёт процессу зависнуть на подтверждении. Для чтения это
безопасно. Для записи с `--yes` stdin тоже не нужен.

Node.js/TypeScript без дополнительной библиотеки:

```javascript
import { execFile } from "node:child_process";
import { promisify } from "node:util";

const runFile = promisify(execFile);
const timetk = "/opt/time-toolkit/.venv/bin/timetk";

export async function searchTime(profile, query) {
  const { stdout } = await runFile(
    timetk,
    ["--profile", profile, "--format", "json", "search", query, "--since", "7d"],
    { timeout: 45_000, maxBuffer: 10 * 1024 * 1024 },
  );
  const payload = JSON.parse(stdout);
  if (payload.schema_version !== "1.0" || payload.profile !== profile) {
    throw new Error("Unexpected Time Toolkit response");
  }
  return payload.data;
}
```

`execFile` передаёт аргументы без shell и снижает риск command injection. Не
собирайте строку вида `exec("timetk ... " + userInput)`.

## Поток NDJSON

`watch` подходит для реакции на новые сообщения без частого polling:

```javascript
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

const child = spawn(
  "/opt/time-toolkit/.venv/bin/timetk",
  [
    "--profile", "university", "--format", "ndjson", "watch",
    "--event", "posted", "--lifecycle",
  ],
  { stdio: ["ignore", "pipe", "inherit"] },
);

const lines = createInterface({ input: child.stdout });
for await (const line of lines) {
  const envelope = JSON.parse(line);
  if (envelope.schema_version !== "1.0" || envelope.profile !== "university") continue;
  if (envelope.meta?.kind === "lifecycle") {
    await catchUpTimeHistory({ overlap: true });
    continue;
  }
  await handleTimeEvent(envelope.data);
}

if (child.exitCode !== 0) {
  throw new Error(`timetk watch exited with ${child.exitCode}`);
}
```

Consumer должен быть идемпотентным. WebSocket удаляет недавние дубли только в
памяти текущего процесса; после перезапуска то же событие может прийти снова.
Храните пару `(profile, semantic_key)`, `post_id`, `update_at` и собственный
checkpoint. `semantic_key` не зависит от WebSocket `seq` и одинаков для логического
повтора после reconnect. Известные события используют `rt1:`, а неизвестные могут
использовать `rt2:` с routing context. Считайте весь ключ непрозрачным и не
проверяйте его префикс. Он server-local, поэтому профиль должен быть частью ключа
внешней базы.

События имеют upstream-форму Mattermost WebSocket, а вложенные JSON-строки
`post`, `reaction` и `preference` уже декодированы. Конкретный набор полей зависит
от версии Time, поэтому игнорируйте неизвестные поля. К исходным `event`, `data`,
`broadcast` и `seq` аддитивно добавляются `channel_id`, `post_id`, типизированный
`post` и `semantic_key`; `schema_version` остаётся `1.0`.

WebSocket не является durable queue. Запускайте `watch` с `--lifecycle` и после
каждой lifecycle-строки выполняйте REST catch-up от последнего сохранённого
timestamp с небольшим overlap. Затем повторно отбрасывайте известные
`semantic_key`, `post_id` и `update_at`. Только после успешного catch-up продвигайте
checkpoint. Lifecycle-строка всегда предшествует обычным событиям нового
соединения. Time Toolkit не создаёт постоянное зеркало и не меняет read state во
время чтения истории.

## Локальный HTTP-клиент

HTTP удобен, если основной сервис не должен запускать subprocess на каждый запрос:

```typescript
const baseUrl = "http://127.0.0.1:8765";
const key = process.env.TIME_TOOLKIT_SERVICE_API_KEY;

export async function unread(profile: string) {
  const response = await fetch(
    `${baseUrl}/v1/${encodeURIComponent(profile)}/unread?include_posts=true&limit=50`,
    {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(45_000),
    },
  );
  if (!response.ok) throw new Error(`Time Toolkit HTTP ${response.status}`);
  const payload = await response.json();
  if (payload.schema_version !== "1.0") throw new Error("Unsupported schema");
  return payload.data;
}
```

Сам ключ открывает все настроенные профили процесса. Если одному проекту нужен
только `university`, запускайте отдельный экземпляр с отдельным config dir, а не
полагайтесь на честность URL клиента.

## Прямой Python API

```python
from time_toolkit.service import TimeService


def recent_posts(channel: str):
    with TimeService.open("university") as time:
        return time.channel_posts(channel, limit=50)
```

Для async-приложения создавайте сервис внутри worker thread. Не делитесь одним
`TimeService` между запросами и потоками. Полные signatures и модели есть в
[справочнике Python](python-api.md).

## MCP в другом агенте

Любой MCP-клиент с поддержкой STDIO может запустить:

```json
{
  "mcpServers": {
    "time-toolkit": {
      "command": "/opt/time-toolkit/.venv/bin/timetk",
      "args": ["mcp"]
    }
  }
}
```

Название поля верхнего уровня зависит от клиента. Независимо от клиента
применяйте policy: read tools можно выполнять сразу, `time_commit_write` — только
после показа плана и нового явного согласия. Клиент без возможности enforce
approval следует ограничить чтением или отключить ему MCP-профиль.

## Автоматическая запись из сервиса

Автоматизация разрешена только для профиля с `write_policy=fullauto`. Безопаснее
создать отдельный профиль и bot token с минимальными серверными правами, чем
использовать пользовательскую сессию. Оператор включает режим явно:

```bash
timetk profile update automation --write-policy fullauto
```

Затем внешний проект проверяет свои правила до запуска CLI:

```python
import hashlib
import subprocess

ALLOWED_PROFILES = {"automation"}
ALLOWED_CHANNELS = {"announcements", "course-updates"}


def publish(profile: str, channel: str, message: str, event_id: str):
    if profile not in ALLOWED_PROFILES or channel not in ALLOWED_CHANNELS:
        raise PermissionError("Target is outside the integration allowlist")
    key = "event-" + hashlib.sha256(event_id.encode()).hexdigest()[:32]
    return subprocess.run(
        [
            "/opt/time-toolkit/.venv/bin/timetk",
            "--profile",
            profile,
            "--format",
            "json",
            "post",
            channel,
            "--message",
            message,
            "--idempotency-key",
            key,
            "--yes",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
```

Allowlist должен опираться на канонические channel IDs или точные имена после
предварительного разрешения, а не на пользовательский substring. Отдельно
ограничьте авторов запроса, длину и тип текста, частоту, объём файлов и время
отправки. Time Toolkit проверяет профильную политику, но не знает этих правил.

Для двухэтапного бизнес-процесса храните статус `draft → approved → sent` во
внешней базе. Передавайте `write_mode="confirmed"` в Python только на переходе
`approved → sent`, где есть неизменяемый approved payload и audit record.

## Idempotency и повторы

`TimeClient` по умолчанию делает до трёх попыток для безопасных HTTP-методов и для
записей с idempotency key. Повторяются network errors и ответы 429, 502, 503, 504.
`Retry-After` учитывается, задержка ограничена 30 секундами.

Внешний сервис должен:

1. дать одной логической операции стабильный idempotency key;
2. при timeout не создавать новый key до проверки результата;
3. не повторять `delete`, `edit` или другую запись бесконечно;
4. хранить свой event ID и результат Time, если операция важна;
5. различать retryable code `6` и постоянные 2/3/4/5/7;
6. ставить общий deadline больше внутреннего timeout, но конечный.

Mattermost-совместимость idempotency зависит от endpoint и версии сервера. Даже с
ключом проектируйте consumer так, чтобы повтор не приводил к критичному эффекту.

## Объём данных и пагинация

Не запрашивайте всю историю в каждом цикле. Используйте `--since`, channel filter
и разумный `--limit`. Для polling храните timestamp последней успешно обработанной
записи с небольшим overlap, затем дедуплицируйте по post ID. Это компенсирует
одинаковые timestamps и задержанное появление записи в поиске.

`search` зависит от серверного Mattermost search и может отличаться между форками.
Для полного чтения известного канала надёжнее `posts CHANNEL` с временной границей.

## Секреты и данные

Внешнему проекту не обязательно знать токен Time:

- CLI, MCP и локальный HTTP-процесс могут читать его из keyring;
- HTTP-потребитель получает отдельный service key;
- `timetk -o ndjson watch` может работать отдельным gateway-процессом, а consumer
  получать только выбранные события из stdout;
- Python-процесс с `TimeService` или `RealtimeService` имеет доступ к токену через
  `SecretStore`, поэтому его нельзя считать изолированным consumer;
- если контейнеру действительно нужен прямой доступ, он получает только токен
  нужного профиля как runtime secret.

Не включайте stdout/stderr команды в telemetry без фильтрации. В stdout есть
сообщения и профили, в stderr — тексты ошибок и иногда названия кандидатов каналов.
Задайте retention, access control и удаление для своих логов, очередей и баз.

## Версионирование интеграции

Закрепляйте release tag `v0.4.0` или точный commit. При обновлении:

1. прочитайте [CHANGELOG](../CHANGELOG.md);
2. прогоните contract tests на синтетическом payload;
3. проверьте `schema_version`;
4. выполните `doctor` и одну read-only команду для нужного профиля;
5. проверьте `--dry-run` записи, не отправляя тестовое сообщение в реальный канал;
6. только затем обновляйте production consumer.

Версия пакета следует SemVer. Пока major равен 0, несовместимые изменения Python
API могут появляться в minor-релизе и будут описаны в changelog. Изменение JSON
контракта требует новой `schema_version`.
