#!/bin/zsh

set -eu

PROJECT_DIR="${0:A:h}/.."
cd "$PROJECT_DIR"

if (( $# > 0 )); then
  printf 'This script takes no arguments.\n' >&2
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required: https://docs.astral.sh/uv/getting-started/installation/\n' >&2
  exit 2
fi

uv sync --locked --no-editable --all-extras

if command -v codex >/dev/null 2>&1; then
  if ! codex mcp get time-toolkit >/dev/null 2>&1; then
    codex mcp add time-toolkit -- "$PROJECT_DIR/.venv/bin/timetk" mcp
  fi
else
  printf 'Codex CLI was not found; register later: %s\n' \
    "codex mcp add time-toolkit -- $PROJECT_DIR/.venv/bin/timetk mcp"
fi

printf '%s\n' 'Time Toolkit is installed. It intentionally stops before credentials.'
printf '%s\n' 'Create your own profile, then run the hidden auth set prompt described in docs/getting-started.md.'
