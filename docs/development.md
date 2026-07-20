# Разработка и выпуск версий

Репозиторий содержит только исходники, синтетические тесты, документацию и файлы
сборки. Реальные Time-данные в рабочем дереве запрещены.

## Структура

```text
src/time_toolkit/
  auth.py          keyring, env и HTTP service key
  cli.py           argparse и CLI flows
  client.py        низкоуровневый Mattermost REST API v4
  config.py        профили и атомарный JSON config
  dates.py         даты, ISO 8601 и относительные границы
  errors.py        ошибки и exit codes
  http_api.py      read-only FastAPI adapter
  mcp_server.py    MCP tools и prepared writes
  models.py        нормализованные dataclasses
  output.py        text/json/ndjson
  realtime.py      Mattermost WebSocket
  service.py       общая логика и write policy
tests/              только синтетические данные
docs/               пользовательские и технические справочники
.github/workflows/ci.yml
pyproject.toml
uv.lock
```

## Окружение

```bash
git clone https://github.com/erstcl/time-toolkit.git
cd time-toolkit
uv sync --locked --no-editable --all-extras
```

Проект поддерживает Python 3.11–3.14. `uv.lock` входит в репозиторий. Изменение
dependencies в `pyproject.toml` должно сопровождаться обновлением lockfile через
uv и отдельной проверкой diff.

В этой установке `--no-editable` нужен из-за поведения Python со скрытыми `.pth`.
Он также делает локальный запуск ближе к CI. После изменения source без смены
версии принудительно пересоберите установленный wheel:

```bash
uv sync --locked --no-editable --all-extras --reinstall-package time-toolkit
```

Для быстрого промежуточного теста можно временно задать `PYTHONPATH=src`, но перед
публикацией обязательна проверка пересобранного установленного пакета.

## Проверки

Полная локальная проверка:

```bash
uv sync --locked --no-editable --all-extras --reinstall-package time-toolkit
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest
uv build
```

Автоформатирование:

```bash
uv run --no-sync ruff format .
uv run --no-sync ruff check --fix .
```

Просматривайте diff после `--fix`: он не заменяет review.

CI запускает lint, formatting check и tests на Ubuntu для Python 3.11, 3.12, 3.13
и 3.14, а на Python 3.12 также собирает wheel и sdist. `fail-fast=false`, поэтому
видны результаты всей матрицы.

## Тестовые данные

Тесты используют только вымышленные:

- usernames, email и display names;
- сообщения и каналы;
- tokens, IDs и API payload;
- пути и содержимое файлов.

Нельзя добавлять реальные сообщения, списки пользователей, export JSON/NDJSON,
cookies, tokens, CSRF, service keys, screenshots и скачанные файлы. `.gitignore`
перехватывает типичные exports, но разработчик обязан проверить `git status` и
staged diff вручную.

Сетевые unit tests используют fake/mock client и адреса из зарезервированной зоны
`.test`; они не должны обращаться к живым инстансам. Live smoke test выполняется
отдельно, read-only и не печатает содержимое сообщений в CI.

## Инварианты

Изменение нельзя сливать, если нарушен хотя бы один пункт:

1. structured result явно содержит profile и server;
2. read command не меняет read state;
3. server write требует явный profile и preview/confirmation на пользовательской
   поверхности;
4. новый профиль получает `approval`;
5. `readonly`, `approval` и `fullauto` одинаково применяются к любому имени
   профиля и проверяются непосредственно перед записью;
6. secret не попадает в config, stdout, log или exception;
7. неоднозначный channel не выбирается молча;
8. network loop имеет timeout и ограниченный backoff;
9. неполный download не заменяет итоговый файл;
10. новый машинный формат либо совместим со schema `1.0`, либо получает новую
    schema version.

## Добавление операции чтения

Обычный порядок:

1. добавить минимальный endpoint wrapper в `TimeClient`;
2. преобразовать raw payload и бизнес-семантику в `TimeService`;
3. добавить синтетические unit tests;
4. вывести операцию только в нужных адаптерах;
5. проверить, что она не вызывает endpoint изменения read state;
6. задать MCP tool annotations;
7. описать CLI/MCP/HTTP/Python surface в соответствующем справочнике;
8. проверить ошибки, limit, pagination и пустой результат.

Не возвращайте raw Mattermost payload напрямую, если уже есть модель. Если новое
поле нужно внешнему контракту, добавьте его в dataclass и тест сериализации.

## Добавление операции записи

Запись требует больше проверок:

1. endpoint wrapper принимает idempotency key, если endpoint это допускает;
2. публичный метод `TimeService` первым вызывает `_require_write_allowed()`;
3. CLI строит полный preview до вызова метода и открывает правильный `write_mode`;
4. MCP action валидирует payload, входит в preview и выполняется только через
   `time_commit_write`;
5. HTTP route записи не добавляется без нового security design;
6. тесты доказывают блокировку `readonly`, подтверждённый `approval`, явный
   `fullauto` и двухшаговый MCP flow;
7. документация перечисляет side effects и retry semantics.

Если операция изменяет только локальную конфигурацию или файл, отдельно опишите её
эффект; профильная server-write policy может к ней не относиться.

## Документация как контракт

README остаётся короткой точкой входа. Руководства разделены по назначению:

- getting started ведёт нового пользователя по одному пути;
- CLI, MCP, HTTP и Python — справочники;
- integrations — готовые способы подключения;
- security и architecture объясняют ограничения и решения;
- troubleshooting начинается с наблюдаемого симптома.

При изменении интерфейса найдите его упоминания:

```bash
rg 'old-name|old-option|schema_version' README.md docs CHANGELOG.md src tests
```

Проверяйте ссылки и code blocks. В документации нельзя использовать реальные
каналы, тексты, IDs и пользователей как пример.

## Версионирование

Project version следует [Semantic Versioning](https://semver.org/):

- patch `0.1.1` — совместимый bug/security fix;
- minor `0.2.0` — новая функциональность и, пока major равен 0, возможное
  несовместимое изменение публичного API;
- major `1.0.0` — объявленный стабильный контракт.

Любое несовместимое изменение должно быть явно названо в changelog и release
notes. JSON `schema_version` версионируется отдельно: несовместимое изменение
envelope или полей требует новой major schema.

Источник `__version__` находится в `src/time_toolkit/__init__.py`, package version —
в `pyproject.toml`. Перед релизом они должны совпадать.

## Release checklist

1. Определить version и дату.
2. Обновить `pyproject.toml`, `src/time_toolkit/__init__.py`, lockfile и changelog.
3. Убедиться, что секция release не пуста, а `[Unreleased]` готова для следующих
   изменений.
4. Выполнить полную локальную проверку из этого файла.
5. Просмотреть `git diff --check`, `git status` и staged diff на секреты/exports.
6. Создать branch и pull request с описанием поведения и проверок.
7. Дождаться зелёной матрицы Python 3.11–3.14.
8. Слить PR в `main` и дождаться зелёного push CI для merge commit.
9. Создать annotated tag `vX.Y.Z` на проверенном commit и push tag.
10. Создать GitHub Release из changelog и проверить ссылку/tag/commit.
11. Выполнить read-only `doctor`, `me` и MCP smoke test установленной версии.

Тег и GitHub Release нельзя создавать до зелёного CI на коде релиза.

## Security review перед публикацией репозитория

Перед public release обязательны MIT license, security policy, responsible
disclosure contact, dependency audit и чистая история, созданная из проверенного
snapshot без старого `.git`. Повторный scan охватывает tracked files, commit
metadata, release assets и CI logs. Переключение видимости выполняется только после
ручной проверки точного списка публикуемых файлов.
