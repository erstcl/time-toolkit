from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from time_toolkit import __version__
from time_toolkit.auth import AuthContext
from time_toolkit.errors import (
    AuthenticationError,
    ConflictError,
    NetworkError,
    NotFoundError,
    PermissionError,
    TimeToolkitError,
    UsageError,
)

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = {429, 502, 503, 504}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class TimeClient:
    """Bounded, profile-agnostic client for Time/Mattermost API v4."""

    def __init__(
        self,
        base_url: str,
        auth: AuthContext | None = None,
        *,
        timeout: float = 30.0,
        max_attempts: int = 3,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth or AuthContext("")
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> TimeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _headers(self, *, idempotency_key: str = "") -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"timetk/{__version__}",
        }
        headers.update(self.auth.headers())
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        idempotency_key: str = "",
    ) -> Any:
        verb = method.upper()
        retryable = verb in _SAFE_METHODS or bool(idempotency_key)
        attempts = self.max_attempts if retryable else 1
        last_network_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                log.debug("%s %s%s", verb, self.base_url, path)
                response = self._http.request(
                    verb,
                    path,
                    params=params,
                    json=json_body,
                    headers=self._headers(idempotency_key=idempotency_key),
                    timeout=self.timeout,
                )
            except httpx.RequestError as exc:
                last_network_error = exc
                if attempt == attempts:
                    break
                time.sleep(min(0.5 * 2 ** (attempt - 1), 4.0))
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
                retry_after = response.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = 0.5 * 2 ** (attempt - 1)
                time.sleep(min(max(delay, 0.0), 30.0))
                continue

            self._raise_for_status(response)
            if response.status_code == 204 or not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise TimeToolkitError(
                    f"Time returned a non-JSON response for {verb} {path}",
                    status_code=response.status_code,
                ) from exc

        raise NetworkError(f"Cannot reach Time server at {self.base_url}") from last_network_error

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        message = ""
        request_id = response.headers.get("X-Request-Id", "")
        try:
            body = response.json()
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("id") or "")
                request_id = str(body.get("request_id") or request_id)
        except ValueError:
            pass
        suffix = f" (request {request_id})" if request_id else ""
        if response.status_code == 401:
            raise AuthenticationError(f"Time session is missing, invalid, or expired{suffix}")
        if response.status_code == 403:
            raise PermissionError(f"Time denied this action{suffix}")
        if response.status_code == 404:
            raise NotFoundError(message or f"Time resource not found{suffix}")
        if response.status_code == 409:
            raise ConflictError(message or f"Time reported a conflict{suffix}")
        if response.status_code == 400:
            raise UsageError(message or f"Time rejected the request{suffix}")
        raise TimeToolkitError(
            message or f"Time returned HTTP {response.status_code}{suffix}",
            status_code=response.status_code,
        )

    # System and identity

    def ping(self) -> dict[str, Any]:
        value = self.request("GET", "/api/v4/system/ping")
        return value if isinstance(value, dict) else {}

    def get_websocket_url(self) -> str:
        value = self.request("GET", "/api/v4/config/client", params={"format": "old"})
        if not isinstance(value, dict) or not value.get("WebsocketURL"):
            return ""
        candidate = str(value["WebsocketURL"]).strip().rstrip("/")
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise UsageError("Time returned a malformed WebsocketURL") from exc
        if (
            parsed.scheme not in {"ws", "wss"}
            or not hostname
            or any(character.isspace() for character in hostname)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise UsageError("Time returned an invalid WebsocketURL")
        return candidate

    def get_me(self) -> dict[str, Any]:
        return self.request("GET", "/api/v4/users/me")

    def get_my_teams(self) -> list[dict[str, Any]]:
        value = self.request("GET", "/api/v4/users/me/teams")
        return value if isinstance(value, list) else []

    def get_user(self, user_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v4/users/{user_id}")

    def get_user_by_username(self, username: str) -> dict[str, Any]:
        clean = quote(username.lstrip("@"), safe="")
        return self.request("GET", f"/api/v4/users/username/{clean}")

    def search_users(self, term: str, *, limit: int = 20) -> list[dict[str, Any]]:
        value = self.request(
            "POST",
            "/api/v4/users/search",
            json_body={"term": term, "limit": min(max(limit, 1), 100)},
        )
        return value if isinstance(value, list) else []

    def get_users_by_ids(self, user_ids: list[str]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        unique = list(dict.fromkeys(user_id for user_id in user_ids if user_id))
        for offset in range(0, len(unique), 200):
            value = self.request(
                "POST", "/api/v4/users/ids", json_body=unique[offset : offset + 200]
            )
            if isinstance(value, list):
                output.extend(value)
        return output

    # Channels

    def iter_my_channels(
        self,
        user_id: str,
        team_id: str,
        *,
        per_page: int = 100,
        max_pages: int = 0,
    ) -> Iterator[list[dict[str, Any]]]:
        page = 0
        size = min(max(per_page, 1), 200)
        path = f"/api/v4/users/{user_id}/teams/{team_id}/channels"
        while True:
            value = self.request("GET", path, params={"page": page, "per_page": size})
            batch = value if isinstance(value, list) else []
            if not batch:
                return
            yield batch
            if len(batch) < size:
                return
            page += 1
            if max_pages and page >= max_pages:
                return

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v4/channels/{channel_id}")

    def get_channel_by_name(self, team_id: str, name: str) -> dict[str, Any]:
        clean = quote(name, safe="")
        return self.request("GET", f"/api/v4/teams/{team_id}/channels/name/{clean}")

    def get_sidebar_categories(self, user_id: str, team_id: str) -> dict[str, Any]:
        value = self.request(
            "GET",
            f"/api/v4/users/{user_id}/teams/{team_id}/channels/categories",
        )
        return value if isinstance(value, dict) else {"categories": [], "order": []}

    def get_channels_by_ids(self, channel_ids: list[str]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        unique = list(dict.fromkeys(channel_id for channel_id in channel_ids if channel_id))
        for offset in range(0, len(unique), 200):
            value = self.request(
                "POST", "/api/v4/channels/ids", json_body=unique[offset : offset + 200]
            )
            if isinstance(value, list):
                output.extend(value)
        return output

    def get_channel_members_for_user(self, user_id: str, team_id: str) -> list[dict[str, Any]]:
        value = self.request("GET", f"/api/v4/users/{user_id}/teams/{team_id}/channels/members")
        return value if isinstance(value, list) else []

    def get_team_unread(self, user_id: str, team_id: str) -> dict[str, Any]:
        value = self.request(
            "GET",
            f"/api/v4/users/{user_id}/teams/{team_id}/unread",
            params={"include_collapsed_threads": "true"},
        )
        return value if isinstance(value, dict) else {}

    def get_channel_unread(self, user_id: str, channel_id: str) -> dict[str, Any]:
        value = self.request("GET", f"/api/v4/users/{user_id}/channels/{channel_id}/unread")
        return value if isinstance(value, dict) else {}

    def view_channel(self, user_id: str, channel_id: str) -> dict[str, Any]:
        value = self.request(
            "POST",
            f"/api/v4/channels/members/{user_id}/view",
            json_body={"channel_id": channel_id, "collapsed_threads_supported": True},
        )
        return value if isinstance(value, dict) else {}

    # Posts and threads

    @staticmethod
    def _ordered_posts(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            return []
        posts = value.get("posts") if isinstance(value.get("posts"), dict) else {}
        order = value.get("order") if isinstance(value.get("order"), list) else []
        if order:
            return [posts[post_id] for post_id in order if post_id in posts]
        return sorted(posts.values(), key=lambda post: int(post.get("create_at", 0) or 0))

    def get_post(self, post_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/v4/posts/{post_id}")

    def get_thread(self, root_id: str) -> list[dict[str, Any]]:
        return self._ordered_posts(self.request("GET", f"/api/v4/posts/{root_id}/thread"))

    def get_channel_posts_page(
        self,
        channel_id: str,
        *,
        page: int = 0,
        per_page: int = 100,
        since_ms: int | None = None,
        before: str = "",
        after: str = "",
    ) -> list[dict[str, Any]]:
        params: dict[str, Any]
        if since_ms is not None:
            # Time forbids combining `since` with page/per_page/before/after.
            params = {"since": since_ms}
        else:
            params = {"page": page, "per_page": min(max(per_page, 1), 200)}
            if before:
                params["before"] = before
            if after:
                params["after"] = after
        value = self.request("GET", f"/api/v4/channels/{channel_id}/posts", params=params)
        return self._ordered_posts(value)

    def search_posts(
        self,
        team_id: str,
        terms: str,
        *,
        page: int = 0,
        per_page: int = 100,
        is_or_search: bool = False,
    ) -> list[dict[str, Any]]:
        body = {
            "terms": terms,
            "is_or_search": is_or_search,
            "page": page,
            "per_page": min(max(per_page, 1), 200),
        }
        value = self.request("POST", f"/api/v4/teams/{team_id}/posts/search", json_body=body)
        return self._ordered_posts(value)

    def get_followed_threads(
        self,
        user_id: str,
        team_id: str,
        *,
        page_size: int = 100,
        max_pages: int = 0,
    ) -> list[dict[str, Any]]:
        path = f"/api/v4/users/{user_id}/teams/{team_id}/threads"
        output: list[dict[str, Any]] = []
        before = ""
        page = 0
        size = min(max(page_size, 1), 200)
        while True:
            params: dict[str, Any] = {
                "extended": "true",
                "deleted": "false",
                "totalsOnly": "false",
                "skipTotal": "true",
                "pageSize": size,
            }
            if before:
                params["before"] = before
            value = self.request("GET", path, params=params)
            threads = value.get("threads", []) if isinstance(value, dict) else value
            batch = threads if isinstance(threads, list) else []
            if not batch:
                break
            output.extend(batch)
            if len(batch) < size:
                break
            before = str(batch[-1].get("id", ""))
            if not before:
                break
            page += 1
            if max_pages and page >= max_pages:
                break
        return output

    def get_thread_stats(self, user_id: str, team_id: str) -> dict[str, Any]:
        value = self.request("GET", f"/api/v4/users/{user_id}/teams/{team_id}/threads/stats")
        return value if isinstance(value, dict) else {}

    def follow_thread(
        self,
        user_id: str,
        team_id: str,
        thread_id: str,
        *,
        following: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        method = "PUT" if following else "DELETE"
        value = self.request(
            method,
            f"/api/v4/users/{user_id}/teams/{team_id}/threads/{thread_id}/following",
            idempotency_key=idempotency_key,
        )
        return value if isinstance(value, dict) else {}

    def set_post_unread(
        self, user_id: str, post_id: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        value = self.request(
            "POST",
            f"/api/v4/users/{user_id}/posts/{post_id}/set_unread",
            idempotency_key=idempotency_key,
        )
        return value if isinstance(value, dict) else {}

    def get_posts_around_unread(
        self,
        user_id: str,
        channel_id: str,
        *,
        limit_before: int = 0,
        limit_after: int = 100,
    ) -> list[dict[str, Any]]:
        value = self.request(
            "GET",
            f"/api/v4/users/{user_id}/channels/{channel_id}/posts/unread",
            params={
                "limit_before": min(max(limit_before, 0), 200),
                "limit_after": min(max(limit_after, 1), 200),
                "collapsedThreads": "true",
                "collapsedThreadsExtended": "true",
            },
        )
        return self._ordered_posts(value)

    def get_flagged_posts(
        self,
        user_id: str,
        *,
        team_id: str = "",
        channel_id: str = "",
        page: int = 0,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "page": page,
            "per_page": min(max(per_page, 1), 200),
        }
        if team_id:
            params["team_id"] = team_id
        if channel_id:
            params["channel_id"] = channel_id
        value = self.request("GET", f"/api/v4/users/{user_id}/posts/flagged", params=params)
        if isinstance(value, list):
            output: list[dict[str, Any]] = []
            for item in value:
                if isinstance(item, dict) and "posts" in item:
                    output.extend(self._ordered_posts(item))
                elif isinstance(item, dict):
                    output.append(item)
            return output
        return self._ordered_posts(value)

    def set_flagged(
        self, user_id: str, post_id: str, *, flagged: bool, idempotency_key: str
    ) -> dict[str, Any]:
        preference = {
            "user_id": user_id,
            "category": "flagged_post",
            "name": post_id,
            "value": "true",
        }
        if flagged:
            method = "PUT"
            path = f"/api/v4/users/{user_id}/preferences"
        else:
            method = "POST"
            path = f"/api/v4/users/{user_id}/preferences/delete"
        value = self.request(method, path, json_body=[preference], idempotency_key=idempotency_key)
        return value if isinstance(value, dict) else {}

    def get_pinned_posts(self, channel_id: str) -> list[dict[str, Any]]:
        value = self.request("GET", f"/api/v4/channels/{channel_id}/pinned")
        return self._ordered_posts(value)

    def get_post_reactions(self, post_id: str) -> list[dict[str, Any]]:
        value = self.request("GET", f"/api/v4/posts/{post_id}/reactions")
        return value if isinstance(value, list) else []

    def get_post_readers(self, post_ids: list[str]) -> dict[str, Any]:
        if not post_ids or len(post_ids) > 100:
            raise UsageError("Reader status accepts between 1 and 100 post IDs")
        value = self.request("POST", "/api/v4/posts/readers", json_body={"post_ids": post_ids})
        return value if isinstance(value, dict) else {}

    def create_post(
        self,
        message: str,
        *,
        channel_id: str = "",
        peer: str = "",
        root_id: str = "",
        file_ids: list[str] | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if bool(channel_id) == bool(peer):
            raise UsageError("Specify exactly one of channel_id or peer")
        body: dict[str, Any] = {"message": message, "idempotency_key": idempotency_key}
        body["channel_id" if channel_id else "peer"] = channel_id or peer
        if root_id:
            body["root_id"] = root_id
        if file_ids:
            body["file_ids"] = file_ids
        return self.request(
            "POST",
            "/api/v4/posts",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def update_post(self, post_id: str, message: str, *, idempotency_key: str) -> dict[str, Any]:
        return self.request(
            "PUT",
            f"/api/v4/posts/{post_id}",
            json_body={"id": post_id, "message": message},
            idempotency_key=idempotency_key,
        )

    def delete_post(self, post_id: str, *, idempotency_key: str) -> dict[str, Any]:
        return self.request("DELETE", f"/api/v4/posts/{post_id}", idempotency_key=idempotency_key)

    def pin_post(self, post_id: str, *, pinned: bool, idempotency_key: str) -> dict[str, Any]:
        action = "pin" if pinned else "unpin"
        return self.request(
            "POST", f"/api/v4/posts/{post_id}/{action}", idempotency_key=idempotency_key
        )

    def add_reaction(
        self, user_id: str, post_id: str, emoji: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/v4/reactions",
            json_body={"user_id": user_id, "post_id": post_id, "emoji_name": emoji},
            idempotency_key=idempotency_key,
        )

    def remove_reaction(
        self, user_id: str, post_id: str, emoji: str, *, idempotency_key: str
    ) -> dict[str, Any]:
        return self.request(
            "DELETE",
            f"/api/v4/users/{user_id}/posts/{post_id}/reactions/{emoji}",
            idempotency_key=idempotency_key,
        )

    # Files

    def upload_file(self, channel_id: str, path: Path) -> dict[str, Any]:
        try:
            with path.open("rb") as handle:
                response = self._http.post(
                    "/api/v4/files",
                    data={"channel_id": channel_id},
                    files={"files": (path.name, handle)},
                    headers={
                        key: value
                        for key, value in self._headers().items()
                        if key != "Content-Type"
                    },
                    timeout=self.timeout,
                )
        except OSError as exc:
            raise UsageError(f"Cannot read file: {path}") from exc
        except httpx.RequestError as exc:
            raise NetworkError(f"Cannot upload file to {self.base_url}") from exc
        self._raise_for_status(response)
        value = response.json()
        return value if isinstance(value, dict) else {}

    def download_file_to(self, file_id: str, destination: Path) -> None:
        headers = {key: value for key, value in self._headers().items() if key != "Content-Type"}
        try:
            with self._http.stream(
                "GET",
                f"/api/v4/files/{file_id}",
                headers=headers,
                timeout=self.timeout,
            ) as response:
                self._raise_for_status(response)
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        except OSError as exc:
            raise UsageError(f"Cannot write file: {destination}") from exc
        except httpx.RequestError as exc:
            raise NetworkError(f"Cannot download file from {self.base_url}") from exc

    def get_file_info(self, file_id: str) -> dict[str, Any]:
        value = self.request("GET", f"/api/v4/files/{file_id}/info")
        return value if isinstance(value, dict) else {}
