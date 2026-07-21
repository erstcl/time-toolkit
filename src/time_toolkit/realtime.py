from __future__ import annotations

import hashlib
import json
import logging
import ssl
import time
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from time_toolkit import __version__
from time_toolkit.auth import AuthContext
from time_toolkit.errors import AuthenticationError, NetworkError, UsageError

log = logging.getLogger(__name__)


def _endpoint(parsed, *, default_port: int) -> tuple[str, int]:
    hostname = (parsed.hostname or "").lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UsageError("WebSocket URL contains an invalid port") from exc
    if port == 0:
        raise UsageError("WebSocket URL port must be between 1 and 65535")
    return hostname, default_port if port is None else port


def _allowed_endpoint(value: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(f"wss://{value}")
    except ValueError as exc:
        raise UsageError("Allowed WebSocket host is malformed") from exc
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise UsageError("Allowed WebSocket hosts must be hostnames with optional ports")
    return _endpoint(parsed, default_port=443)


def websocket_url(
    base_url: str,
    advertised_url: str = "",
    *,
    allowed_hosts: tuple[str, ...] | list[str] = (),
) -> str:
    try:
        base = urlsplit(base_url.rstrip("/"))
        parsed = urlsplit((advertised_url or base_url).rstrip("/"))
        base_hostname = base.hostname
        advertised_hostname = parsed.hostname
    except ValueError as exc:
        raise UsageError("Time returned a malformed WebSocket URL") from exc
    expected_schemes = {"ws", "wss"} if advertised_url else {"http", "https"}
    if (
        base.scheme not in {"http", "https"}
        or parsed.scheme not in expected_schemes
        or not base_hostname
        or not advertised_hostname
        or any(character.isspace() for character in base_hostname)
        or any(character.isspace() for character in advertised_hostname)
        or base.username
        or base.password
        or base.query
        or base.fragment
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise UsageError("Time returned an invalid WebSocket URL")
    if base.scheme in {"https", "wss"} and parsed.scheme == "ws":
        raise UsageError("Time advertised an insecure WebSocket URL for an HTTPS profile")
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    base_port = 443 if base.scheme in {"https", "wss"} else 80
    target_port = 443 if scheme == "wss" else 80
    base_endpoint = _endpoint(base, default_port=base_port)
    advertised_endpoint = _endpoint(parsed, default_port=target_port)
    allowed_endpoints = {_allowed_endpoint(value) for value in allowed_hosts}
    if (
        advertised_url
        and advertised_endpoint != base_endpoint
        and advertised_endpoint not in allowed_endpoints
    ):
        host, port = advertised_endpoint
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        raise UsageError(
            "Time advertised a WebSocket host that is not trusted: "
            f"{authority}. Add it explicitly with `timetk profile update PROFILE "
            f"--websocket-host {authority}` after verifying it with the server owner."
        )
    path = parsed.path.rstrip("/")
    if not path.endswith("/api/v4/websocket"):
        path = f"{path}/api/v4/websocket"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def websocket_ssl_context(base_url: str) -> ssl.SSLContext | None:
    if urlsplit(base_url).scheme not in {"https", "wss"}:
        return None
    return httpx.create_ssl_context()


def normalize_event(payload: str | bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as exc:
        raise NetworkError("Time sent an invalid WebSocket message") from exc
    if not isinstance(value, dict):
        raise NetworkError("Time sent an invalid WebSocket event")
    data = value.get("data")
    if isinstance(data, dict):
        for key in ("post", "reaction", "preference"):
            nested = data.get(key)
            if not isinstance(nested, str):
                continue
            try:
                decoded = json.loads(nested)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict | list):
                data[key] = decoded
    return value


def _event_channel_id(event: dict[str, Any]) -> str:
    broadcast = event.get("broadcast")
    if isinstance(broadcast, dict) and broadcast.get("channel_id"):
        return str(broadcast["channel_id"])
    data = event.get("data")
    if not isinstance(data, dict):
        return ""
    if data.get("channel_id"):
        return str(data["channel_id"])
    post = data.get("post")
    if isinstance(post, dict):
        return str(post.get("channel_id", ""))
    reaction = data.get("reaction")
    if isinstance(reaction, dict):
        return str(reaction.get("channel_id", ""))
    return ""


class _RecentEvents:
    def __init__(self, maximum: int = 2_000) -> None:
        self.maximum = maximum
        self._queue: deque[str] = deque()
        self._seen: set[str] = set()

    def add(self, event: dict[str, Any]) -> bool:
        digest = hashlib.sha256(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest in self._seen:
            return False
        self._queue.append(digest)
        self._seen.add(digest)
        while len(self._queue) > self.maximum:
            self._seen.remove(self._queue.popleft())
        return True


class RealtimeClient:
    """Authenticated Mattermost WebSocket reader with bounded reconnect backoff."""

    def __init__(
        self,
        base_url: str,
        auth: AuthContext,
        *,
        advertised_url: str = "",
        allowed_websocket_hosts: tuple[str, ...] | list[str] = (),
        connector: Callable[..., Any] = connect,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.endpoint = websocket_url(
            self.base_url,
            advertised_url,
            allowed_hosts=allowed_websocket_hosts,
        )
        self._connector = connector
        self._recent = _RecentEvents()

    def iter_events(
        self,
        *,
        event_types: set[str] | None = None,
        channel_ids: set[str] | None = None,
        reconnect: bool = True,
        max_reconnects: int = 0,
    ) -> Iterator[dict[str, Any]]:
        selected_events = event_types or set()
        selected_channels = channel_ids or set()
        reconnects = 0
        while True:
            try:
                yield from self._connection_events(selected_events, selected_channels)
            except AuthenticationError:
                raise
            except (OSError, TimeoutError, WebSocketException) as exc:
                if not reconnect or (max_reconnects and reconnects >= max_reconnects):
                    raise NetworkError(f"Time WebSocket disconnected from {self.base_url}") from exc
                reconnects += 1
                delay = min(2 ** (reconnects - 1), 30)
                log.warning("Time WebSocket disconnected; reconnecting in %s seconds", delay)
                time.sleep(delay)
                continue
            if not reconnect or (max_reconnects and reconnects >= max_reconnects):
                return
            reconnects += 1
            time.sleep(min(2 ** (reconnects - 1), 30))

    def _connection_events(
        self, event_types: set[str], channel_ids: set[str]
    ) -> Iterator[dict[str, Any]]:
        with self._connector(
            self.endpoint,
            ssl=websocket_ssl_context(self.endpoint),
            origin=self.base_url,
            additional_headers=self.auth.websocket_headers(),
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=8 * 1024 * 1024,
            user_agent_header=f"timetk/{__version__}",
        ) as connection:
            connection.send(
                json.dumps(
                    {
                        "seq": 1,
                        "action": "authentication_challenge",
                        "data": {"token": self.auth.token},
                    }
                )
            )
            pending: list[dict[str, Any]] = []
            for _ in range(10):
                event = normalize_event(connection.recv(timeout=10))
                if event.get("seq_reply") == 1:
                    if event.get("status") != "OK":
                        raise AuthenticationError("Time rejected WebSocket authentication")
                    break
                pending.append(event)
                if event.get("event") == "hello":
                    break
            else:
                raise AuthenticationError("Time did not confirm WebSocket authentication")

            for event in pending:
                if self._matches(event, event_types, channel_ids) and self._recent.add(event):
                    yield event
            while True:
                event = normalize_event(connection.recv())
                if self._matches(event, event_types, channel_ids) and self._recent.add(event):
                    yield event

    @staticmethod
    def _matches(event: dict[str, Any], event_types: set[str], channel_ids: set[str]) -> bool:
        if event_types and str(event.get("event", "")) not in event_types:
            return False
        return not channel_ids or _event_channel_id(event) in channel_ids
