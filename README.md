[Русская версия](README.ru.md) | English

# Time Toolkit

[![CI](https://github.com/erstcl/time-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/erstcl/time-toolkit/actions/workflows/ci.yml)
[![Python 3.11–3.14](https://img.shields.io/badge/python-3.11%E2%80%933.14-3776AB)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-173B72)](LICENSE)

An independent client and integration toolkit for Time Messenger and
Mattermost-compatible servers. One Python package provides the `timetk` CLI, a
`time-toolkit` MCP server, an importable Python API, a WebSocket event stream, and a
local read-only HTTP API.

Time Toolkit needs no server plugin. It uses the permissions of a selected user or
bot through the public REST API v4 and does not change read state during ordinary
read operations.

## Capabilities

- inspect teams, channels, direct messages, posts, threads, unread messages,
  mentions, pins, flags, read receipts, reactions, users, and file metadata;
- search messages and users with machine-readable `text`, `json`, and `ndjson`
  output;
- create, reply to, edit, and delete posts with an explicit write policy;
- manage reactions, pins, flags, followed threads, files, and read state;
- stream Mattermost WebSocket events with filtering, reconnection, and
  deduplication;
- expose tools to Codex and other clients through MCP;
- embed the same service layer in Python applications;
- serve a protected local read-only HTTP API;
- isolate any number of profiles with separate servers, tokens, and write modes.

## Safety model

Every profile has one of three write modes:

| Mode | Interactive CLI and confirmed MCP | Automation and `--yes` |
|---|---:|---:|
| `readonly` | blocked | blocked |
| `approval` | allowed after confirmation | blocked |
| `fullauto` | allowed | allowed |

New profiles default to `approval`. MCP always uses a two-step write flow with a
single-use confirmation token, including for `fullauto` profiles. Tokens are stored
in the operating-system keyring rather than Git or `config.json`.

## Quick start

Time Toolkit requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/erstcl/time-toolkit.git
cd time-toolkit
uv sync --locked --no-editable --all-extras

# Generic Mattermost-compatible server
uv run --no-sync timetk profile add example https://time.example.com

# Explicitly supported Time Messenger deployment
uv run --no-sync timetk profile add university https://time.cu.ru
```

Store and verify a personal access token through hidden input:

```bash
uv run --no-sync timetk -p university auth set
uv run --no-sync timetk -p university auth status --check
```

Start with read-only commands:

```bash
uv run --no-sync timetk -p university channels --pattern general
uv run --no-sync timetk -p university unread --with-posts
uv run --no-sync timetk -p university search "exam" --since 7d
```

Preview and confirm a write operation:

```bash
uv run --no-sync timetk -p university post general -m "Hello" --dry-run
uv run --no-sync timetk -p university post general -m "Hello"
```

`--dry-run` never calls a write endpoint. Without `--yes`, the CLI shows the exact
plan and asks `Proceed? [y/N]`. Automated writers require a separately configured
`fullauto` profile.

## Interfaces

| Use case | Interface |
|---|---|
| Human operator in a terminal | `timetk` CLI |
| Codex or another agent | `time-toolkit` MCP server |
| In-process Python application | `TimeService` |
| Local service written in another language | read-only HTTP API |
| Reaction to new events | `timetk -o ndjson watch` |
| Short language-agnostic script | CLI with `-o json` or `-o ndjson` |

Automated sending from an external service should use a dedicated bot token, a
`fullauto` profile, and an application-level recipient allowlist. The built-in HTTP
API intentionally remains read-only.

## Architecture

```text
CLI / MCP / Python API / HTTP API / WebSocket watcher
                         │
                    TimeService
                         │
             authenticated REST API v4 client
                         │
       Time Messenger / Mattermost-compatible server
```

All interfaces share the same configuration, authentication, service, and safety
layers. Network code stays behind a typed client, while CLI and MCP adapters handle
presentation and confirmation semantics.

## Documentation

- [Getting started, profiles, and tokens](docs/getting-started.md)
- [Complete CLI reference](docs/cli.md)
- [MCP integration](docs/mcp.md)
- [Integration patterns](docs/integrations.md)
- [Python API](docs/python-api.md)
- [HTTP API](docs/http-api.md)
- [Security model](docs/security.md)
- [Architecture and guarantees](docs/architecture.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development and releases](docs/development.md)
- [Changelog](CHANGELOG.md)

## Scope

Time Toolkit is not part of, or endorsed by, Time Messenger, Mattermost, Inc.,
Central University, or operators of compatible servers. Product names and
trademarks belong to their respective owners.

The project intentionally excludes password login, browser-profile scraping,
persistent message mirroring, automatic `mark-read`, HTTP write endpoints, a
full-screen TUI, and Mattermost administration. The rationale is documented in the
[architecture guide](docs/architecture.md#сознательные-ограничения).

Time Toolkit is independently implemented against documented network interfaces and
released under the [MIT License](LICENSE).
