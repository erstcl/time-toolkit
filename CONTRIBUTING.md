# Contributing

Small, focused pull requests are preferred. Start with an issue when a change adds
a public command, changes a JSON field, broadens write access or affects token
handling.

## Development setup

```bash
git clone https://github.com/erstcl/time-toolkit.git
cd time-toolkit
uv sync --locked --no-editable --all-extras
```

After changing source code, reinstall the non-editable package and run all checks:

```bash
uv sync --locked --no-editable --all-extras --reinstall-package time-toolkit
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync pytest
uv build
```

## Data rules

- Use only synthetic users, messages, IDs, tokens and `.test` domains.
- Never commit cookies, credentials, exports, screenshots or downloaded files.
- Read operations must not change read state.
- Every write must name a profile and pass the configured write policy.
- MCP writes must retain the prepare, explicit approval and one-time commit flow.
- Machine-readable changes require tests and documentation.

By submitting a contribution, you agree that it is licensed under the MIT License
and that you have the right to provide it.
