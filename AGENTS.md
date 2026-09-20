# Project instructions

## Agent onboarding

For a clean install, read `README.md`, `docs/getting-started.md`, and `docs/mcp.md`. On macOS, `scripts/bootstrap_agent.sh` installs locked dependencies and registers MCP without touching credentials.
Install the release with `uv sync --locked --no-editable --all-extras`; register
`/absolute/path/to/time-toolkit/.venv/bin/timetk mcp` in the MCP client.
A human must supply their own authorised Time token through the hidden `auth set`
prompt. Default to the `readonly` profile policy and do not request, print, or
reuse another person's token.


- Never print, log, commit, or request Time tokens, cookies, passwords, or CSRF values.
- Never add real Time messages, channel exports, user lists, or downloaded files as fixtures.
- Use synthetic fixtures in tests.
- Keep the explicitly selected profile and server in every structured result.
- Read operations must not change read state.
- Writes must name a profile explicitly and support preview/confirmation.
- Apply `readonly`, `approval`, and `fullauto` identically to every profile name.
- Use only synthetic `.test` domains and synthetic messages in tests.
- Run `uv sync --no-editable --all-extras`, then `uv run --no-sync pytest` and
  `uv run --no-sync ruff check .` before publishing changes.
