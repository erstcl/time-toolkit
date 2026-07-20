from __future__ import annotations

import json

import httpx
import pytest

from time_toolkit.auth import AuthContext
from time_toolkit.client import TimeClient
from time_toolkit.errors import AuthenticationError, TimeToolkitError


def make_client(handler, *, auth: AuthContext | None = None, attempts: int = 3) -> TimeClient:
    transport = httpx.MockTransport(handler)
    http = httpx.Client(base_url="https://time.example.test", transport=transport)
    return TimeClient(
        "https://time.example.test",
        auth or AuthContext("secret"),
        http_client=http,
        max_attempts=attempts,
    )


def test_since_is_not_combined_with_pagination():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"order": [], "posts": {}})

    client = make_client(handler)
    client.get_channel_posts_page("c" * 26, since_ms=123456)
    assert seen == {"since": "123456"}


def test_search_pagination_is_sent_in_json_body():
    recorded: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["query"] = str(request.url.query)
        recorded["body"] = json.loads(request.content)
        return httpx.Response(200, json={"order": [], "posts": {}})

    client = make_client(handler)
    client.search_posts("t" * 26, "migration", page=2, per_page=75)
    assert recorded["query"] == "b''"
    assert recorded["body"] == {
        "terms": "migration",
        "is_or_search": False,
        "page": 2,
        "per_page": 75,
    }


def test_followed_threads_uses_time_page_size_parameter():
    queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(dict(request.url.params))
        return httpx.Response(200, json={"threads": []})

    client = make_client(handler)
    client.get_followed_threads("u" * 26, "t" * 26, page_size=80)
    assert queries[0]["pageSize"] == "80"
    assert "per_page" not in queries[0]


def test_cookie_auth_never_becomes_a_query_parameter():
    headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        headers.update(request.headers)
        return httpx.Response(200, json={"status": "OK"})

    client = make_client(handler, auth=AuthContext("cookie-value", "cookie", "csrf-value"))
    client.ping()
    assert headers["cookie"] == "MMAUTHTOKEN=cookie-value"
    assert headers["x-csrf-token"] == "csrf-value"
    assert "authorization" not in headers


def test_429_retry_is_bounded():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0"}, json={"message": "slow"})

    client = make_client(handler, attempts=3)
    with pytest.raises(TimeToolkitError):
        client.ping()
    assert calls == 3


def test_authentication_error_is_specific():
    client = make_client(lambda _: httpx.Response(401, json={"message": "expired"}))
    with pytest.raises(AuthenticationError):
        client.get_me()


def test_create_post_carries_idempotency_in_body_and_header():
    recorded: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["headers"] = dict(request.headers)
        recorded["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={"id": "p" * 26, "channel_id": "c" * 26, "message": "hello"},
        )

    client = make_client(handler)
    client.create_post("hello", peer="@alex", idempotency_key="once")
    assert recorded["body"]["idempotency_key"] == "once"
    assert recorded["headers"]["idempotency-key"] == "once"


def test_path_values_are_percent_encoded():
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.raw_path.decode())
        return httpx.Response(200, json={})

    client = make_client(handler)
    client.get_user_by_username("@user/name")
    client.get_channel_by_name("t" * 26, "channel/name")
    assert paths == [
        "/api/v4/users/username/user%2Fname",
        f"/api/v4/teams/{'t' * 26}/channels/name/channel%2Fname",
    ]


def test_unflag_sends_complete_preference():
    recorded: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        recorded["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = make_client(handler)
    client.set_flagged("u" * 26, "p" * 26, flagged=False, idempotency_key="once")
    assert recorded["body"] == [
        {
            "user_id": "u" * 26,
            "category": "flagged_post",
            "name": "p" * 26,
            "value": "true",
        }
    ]


def test_websocket_url_is_read_from_public_client_config():
    client = make_client(
        lambda _: httpx.Response(200, json={"WebsocketURL": "wss://ws.time.example.test/"})
    )
    assert client.get_websocket_url() == "wss://ws.time.example.test"


def test_malformed_websocket_url_from_server_is_rejected():
    client = make_client(lambda _: httpx.Response(200, json={"WebsocketURL": "wss://[::1"}))
    with pytest.raises(TimeToolkitError, match="malformed WebsocketURL"):
        client.get_websocket_url()
