from __future__ import annotations

import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from time_toolkit.config import ConfigStore
from time_toolkit.dates import parse_time_bound
from time_toolkit.errors import PermissionError, TimeToolkitError, UsageError
from time_toolkit.models import primitive
from time_toolkit.service import TimeService, WriteMode

WriteAction = Literal[
    "post",
    "reply",
    "edit",
    "delete",
    "pin",
    "unpin",
    "react",
    "unreact",
    "flag",
    "unflag",
    "follow",
    "unfollow",
    "mark-unread",
    "mark-read",
    "upload-files",
]

INSTRUCTIONS = """Time Messenger access through explicitly named profiles. Reading tools do not
change message state unless their name explicitly says so. For every write: first call
time_prepare_write, show its complete preview to the user, wait for explicit approval, and only
then call time_commit_write with the exact confirmation phrase. Never infer approval from an
earlier request. Never expose authentication tokens or silently substitute one profile for
another. A profile's write policy is always enforced in addition to this MCP confirmation flow."""

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
PREPARE_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)

mcp = FastMCP("Time Toolkit", instructions=INSTRUCTIONS)


def _bounded_limit(value: int, *, maximum: int = 200) -> int:
    if value < 1 or value > maximum:
        raise UsageError(f"limit must be between 1 and {maximum}")
    return value


def _envelope(profile: str, server: str, data: Any, **meta: Any) -> dict[str, Any]:
    return primitive(
        {
            "schema_version": "1.0",
            "profile": profile,
            "server": server,
            "data": data,
            "meta": meta,
        }
    )


@contextmanager
def _service(profile: str, *, write_mode: WriteMode = "automated"):
    config = ConfigStore()
    selected = config.get_profile(profile)
    if not selected.mcp_enabled:
        raise UsageError(f"MCP access is disabled for profile {selected.name}")
    with TimeService.open(selected.name, config=config, write_mode=write_mode) as service:
        yield service


def _tool_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except TimeToolkitError as exc:
            raise ToolError(exc.message) from exc

    return wrapped


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_profiles() -> dict[str, Any]:
    """List configured profiles and whether each profile permits MCP access."""
    config = ConfigStore()
    default = config.default_profile_name()
    rows = [
        {**primitive(profile), "default": profile.name == default}
        for profile in config.list_profiles()
    ]
    return _envelope("", "", rows)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_me(profile: str) -> dict[str, Any]:
    """Show the current Time user for an explicit profile."""
    with _service(profile) as service:
        return _envelope(profile, service.profile.base_url, service.me())


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_channels(
    profile: str,
    pattern: str = "",
    channel_type: Literal["", "O", "P", "D", "G"] = "",
    limit: int = 100,
) -> dict[str, Any]:
    """List visible channels and direct-message conversations."""
    with _service(profile) as service:
        rows = service.list_channels(
            pattern=pattern,
            channel_type=channel_type,
            limit=_bounded_limit(limit),
            max_pages=20,
        )
        return _envelope(profile, service.profile.base_url, rows)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_unread(
    profile: str,
    channel: str = "",
    include_posts: bool = False,
    mentions_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """Show unread counts, optionally including unread posts; this does not mark anything read."""
    with _service(profile) as service:
        data = service.unread(
            channel=channel,
            with_posts=include_posts,
            mentions_only=mentions_only,
            limit=_bounded_limit(limit),
        )
        return _envelope(profile, service.profile.base_url, data)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_posts(
    profile: str,
    channel: str,
    since: str = "",
    until: str = "",
    authors: list[str] | None = None,
    contains: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Read posts from a channel without changing read state."""
    with _service(profile) as service:
        rows = service.channel_posts(
            channel,
            since_ms=parse_time_bound(since or None, timezone_name=service.profile.timezone),
            until_ms=parse_time_bound(
                until or None,
                end_of_day=True,
                timezone_name=service.profile.timezone,
            ),
            authors=authors,
            contains=contains,
            limit=_bounded_limit(limit),
        )
        return _envelope(profile, service.profile.base_url, rows)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_search(
    profile: str,
    query: str,
    channels: list[str] | None = None,
    authors: list[str] | None = None,
    since: str = "",
    until: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Search visible posts with optional channel, author, and exact time filters."""
    with _service(profile) as service:
        rows = service.search_posts(
            query,
            channels=channels,
            authors=authors,
            since_ms=parse_time_bound(since or None, timezone_name=service.profile.timezone),
            until_ms=parse_time_bound(
                until or None,
                end_of_day=True,
                timezone_name=service.profile.timezone,
            ),
            limit=_bounded_limit(limit),
        )
        return _envelope(profile, service.profile.base_url, rows)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_thread(profile: str, target: str) -> dict[str, Any]:
    """Read a full thread by root-post ID or Time URL."""
    with _service(profile) as service:
        return _envelope(profile, service.profile.base_url, service.thread(target))


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_followed_threads(
    profile: str, limit: int = 100, include_posts: bool = False
) -> dict[str, Any]:
    """List the current user's followed threads and their unread counts."""
    with _service(profile) as service:
        rows = service.followed_threads(limit=_bounded_limit(limit), with_posts=include_posts)
        return _envelope(profile, service.profile.base_url, rows)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_flagged(profile: str, channel: str = "", limit: int = 100) -> dict[str, Any]:
    """List posts flagged by the current user."""
    with _service(profile) as service:
        rows = service.flagged_posts(channel=channel, limit=_bounded_limit(limit))
        return _envelope(profile, service.profile.base_url, rows)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_pinned(profile: str, channel: str) -> dict[str, Any]:
    """List pinned posts in a channel."""
    with _service(profile) as service:
        return _envelope(profile, service.profile.base_url, service.pinned_posts(channel))


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_user_activity(
    profile: str,
    username: str,
    since: str = "",
    until: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Show a user's visible recent posts and per-channel activity."""
    with _service(profile) as service:
        data = service.user_activity(
            username,
            since_ms=parse_time_bound(since or None, timezone_name=service.profile.timezone),
            until_ms=parse_time_bound(
                until or None,
                end_of_day=True,
                timezone_name=service.profile.timezone,
            ),
            limit=_bounded_limit(limit),
        )
        return _envelope(profile, service.profile.base_url, data)


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_reactions(profile: str, target: str) -> dict[str, Any]:
    """List reactions on a post."""
    with _service(profile) as service:
        return _envelope(profile, service.profile.base_url, service.reactions(target))


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_readers(profile: str, targets: list[str]) -> dict[str, Any]:
    """Show reader receipts for between one and one hundred owned posts."""
    with _service(profile) as service:
        return _envelope(profile, service.profile.base_url, service.readers(targets))


@mcp.tool(annotations=READ_ONLY)
@_tool_errors
def time_file_info(profile: str, file_id: str) -> dict[str, Any]:
    """Show metadata for an attached file without downloading it."""
    with _service(profile) as service:
        return _envelope(profile, service.profile.base_url, service.file_info(file_id))


@dataclass(frozen=True, slots=True)
class PreparedWrite:
    id: str
    profile: str
    server: str
    action: WriteAction
    target: str
    message: str
    emoji: str
    file_ids: tuple[str, ...]
    file_paths: tuple[str, ...]
    created_at: int
    expires_at: float

    @property
    def confirmation(self) -> str:
        return f"CONFIRM {self.id}"

    def preview(self) -> dict[str, Any]:
        return {
            "operation_id": self.id,
            "profile": self.profile,
            "server": self.server,
            "action": self.action,
            "target": self.target,
            "message": self.message,
            "emoji": self.emoji,
            "file_ids": list(self.file_ids),
            "file_paths": list(self.file_paths),
            "created_at": self.created_at,
            "expires_in_seconds": max(0, int(self.expires_at - time.monotonic())),
            "confirmation": self.confirmation,
        }


class PreparedWriteStore:
    def __init__(self, *, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PreparedWrite] = {}
        self._lock = threading.Lock()

    def prepare(
        self,
        *,
        profile: str,
        server: str,
        action: WriteAction,
        target: str,
        message: str = "",
        emoji: str = "",
        file_ids: list[str] | None = None,
        file_paths: list[str] | None = None,
    ) -> PreparedWrite:
        files = tuple(file_ids or ())
        paths = tuple(file_paths or ())
        self._validate(action, target, message, emoji, files, paths)
        now = time.monotonic()
        item = PreparedWrite(
            id=secrets.token_urlsafe(12),
            profile=profile,
            server=server,
            action=action,
            target=target,
            message=message,
            emoji=emoji,
            file_ids=files,
            file_paths=paths,
            created_at=int(time.time()),
            expires_at=now + self.ttl_seconds,
        )
        with self._lock:
            self._purge(now)
            self._items[item.id] = item
        return item

    def claim(self, operation_id: str, confirmation: str) -> PreparedWrite:
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            item = self._items.get(operation_id)
            if item is None:
                raise UsageError("Prepared operation was not found, expired, or already used")
            if confirmation != item.confirmation:
                raise UsageError("Confirmation phrase does not match the prepared operation")
            del self._items[operation_id]
        return item

    def _purge(self, now: float) -> None:
        expired = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired:
            del self._items[key]

    @staticmethod
    def _validate(
        action: WriteAction,
        target: str,
        message: str,
        emoji: str,
        file_ids: tuple[str, ...],
        file_paths: tuple[str, ...],
    ) -> None:
        if not target.strip():
            raise UsageError("target cannot be empty")
        if action in {"post", "reply"} and not message.strip() and not file_ids:
            raise UsageError("A post or reply needs a message or file IDs")
        if action == "edit" and not message.strip():
            raise UsageError("An edited post needs a non-empty message")
        if action in {"react", "unreact"} and not emoji.strip():
            raise UsageError("A reaction needs an emoji name")
        if action == "upload-files" and not file_paths:
            raise UsageError("upload-files needs at least one local file path")
        if action != "upload-files" and file_paths:
            raise UsageError("file_paths are only valid for upload-files")


_prepared_writes = PreparedWriteStore()


@mcp.tool(annotations=PREPARE_ONLY)
@_tool_errors
def time_prepare_write(
    profile: str,
    action: WriteAction,
    target: str,
    message: str = "",
    emoji: str = "",
    file_ids: list[str] | None = None,
    file_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Prepare an exact write preview. This does not change Time; show it to the user."""
    config = ConfigStore()
    selected = config.get_profile(profile)
    if not selected.mcp_enabled:
        raise UsageError(f"MCP access is disabled for profile {selected.name}")
    if selected.write_policy == "readonly":
        raise PermissionError(f"Writes are disabled for profile {selected.name}")
    item = _prepared_writes.prepare(
        profile=selected.name,
        server=selected.base_url,
        action=action,
        target=target,
        message=message,
        emoji=emoji,
        file_ids=file_ids,
        file_paths=file_paths,
    )
    return _envelope(selected.name, selected.base_url, item.preview(), prepared=True)


def _execute_write(service: TimeService, item: PreparedWrite) -> Any:
    key = f"mcp-{item.id}"
    if item.action == "post":
        return service.create_post(
            item.target,
            item.message,
            file_ids=list(item.file_ids),
            idempotency_key=key,
        )
    if item.action == "reply":
        return service.reply(
            item.target,
            item.message,
            file_ids=list(item.file_ids),
            idempotency_key=key,
        )
    if item.action == "edit":
        return service.edit_post(item.target, item.message)
    if item.action == "delete":
        return service.delete_post(item.target)
    if item.action in {"pin", "unpin"}:
        return service.pin_post(item.target, pinned=item.action == "pin")
    if item.action in {"react", "unreact"}:
        return service.react(item.target, item.emoji, add=item.action == "react")
    if item.action in {"flag", "unflag"}:
        return service.flag_post(item.target, flagged=item.action == "flag")
    if item.action in {"follow", "unfollow"}:
        return service.follow_thread(item.target, following=item.action == "follow")
    if item.action == "mark-unread":
        return service.mark_unread(item.target)
    if item.action == "mark-read":
        return service.mark_channel_read(item.target)
    if item.action == "upload-files":
        return {
            "file_ids": service.upload_files(
                item.target, [Path(value).expanduser() for value in item.file_paths]
            )
        }
    raise UsageError(f"Unsupported write action: {item.action}")


@mcp.tool(annotations=WRITE)
@_tool_errors
def time_commit_write(
    operation_id: str,
    confirmation: str,
) -> dict[str, Any]:
    """Execute one prepared write after the user explicitly approved its complete preview."""
    item = _prepared_writes.claim(operation_id, confirmation)
    with _service(item.profile, write_mode="confirmed") as service:
        result = _execute_write(service, item)
        return _envelope(
            item.profile,
            service.profile.base_url,
            result,
            operation_id=item.id,
            committed=True,
        )


def run_mcp_server() -> None:
    mcp.run(transport="stdio")
