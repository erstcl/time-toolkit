from __future__ import annotations

import json
import ssl

import pytest

pytest.importorskip("websockets")

from time_toolkit.auth import AuthContext
from time_toolkit.errors import UsageError
from time_toolkit.realtime import (
    RealtimeClient,
    normalize_event,
    websocket_ssl_context,
    websocket_url,
)


class FakeConnection:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = iter(json.dumps(message) for message in messages)
        self.sent: list[dict[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_: object):
        return None

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def recv(self, timeout: float | None = None) -> str:
        del timeout
        return next(self.messages)


def test_websocket_url_uses_secure_scheme():
    assert websocket_url("https://time.example.test") == "wss://time.example.test/api/v4/websocket"
    assert (
        websocket_url(
            "https://time.example.test",
            "wss://socket.example.test",
            allowed_hosts=["socket.example.test"],
        )
        == "wss://socket.example.test/api/v4/websocket"
    )


def test_websocket_url_rejects_untrusted_cross_host_and_downgrade():
    with pytest.raises(UsageError, match="not trusted"):
        websocket_url("https://time.example.test", "wss://untrusted.invalid")
    with pytest.raises(UsageError, match="insecure"):
        websocket_url("https://time.example.test", "ws://time.example.test")
    with pytest.raises(UsageError, match="malformed"):
        websocket_url("https://time.example.test", "wss://[::1")
    with pytest.raises(UsageError, match="invalid"):
        websocket_url("https://user:password@time.example.test")


def test_websocket_url_requires_explicit_non_default_port():
    with pytest.raises(UsageError, match="not trusted"):
        websocket_url("https://time.example.test", "wss://time.example.test:8443")
    assert (
        websocket_url(
            "https://time.example.test",
            "wss://time.example.test:8443",
            allowed_hosts=["time.example.test:8443"],
        )
        == "wss://time.example.test:8443/api/v4/websocket"
    )


def test_secure_websocket_context_verifies_certificate_and_hostname():
    context = websocket_ssl_context("https://time.example.test")
    assert context is not None
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert websocket_ssl_context("wss://socket.example.test") is not None
    assert websocket_ssl_context("http://localhost:8065") is None


def test_nested_post_is_normalized():
    event = normalize_event(
        json.dumps({"event": "posted", "data": {"post": json.dumps({"id": "post-id"})}})
    )
    assert event["data"]["post"]["id"] == "post-id"


def test_realtime_authenticates_then_filters_and_yields():
    connection = FakeConnection(
        [
            {"status": "OK", "seq_reply": 1},
            {
                "event": "typing",
                "data": {"channel_id": "channel-id"},
                "seq": 2,
            },
            {
                "event": "posted",
                "data": {"post": json.dumps({"id": "post-id", "channel_id": "channel-id"})},
                "broadcast": {"channel_id": "channel-id"},
                "seq": 3,
            },
        ]
    )
    seen: dict[str, object] = {}

    def connector(uri: str, **kwargs):
        seen["uri"] = uri
        seen["kwargs"] = kwargs
        return connection

    client = RealtimeClient(
        "https://time.example.test",
        AuthContext("secret"),
        advertised_url="wss://socket.example.test",
        allowed_websocket_hosts=["socket.example.test"],
        connector=connector,
    )
    event = next(
        client.iter_events(event_types={"posted"}, channel_ids={"channel-id"}, reconnect=False)
    )
    assert event["data"]["post"]["id"] == "post-id"
    assert seen["uri"] == "wss://socket.example.test/api/v4/websocket"
    assert seen["kwargs"]["origin"] == "https://time.example.test"
    assert seen["kwargs"]["additional_headers"] == {
        "Authorization": "Bearer secret",
        "Cookie": "MMAUTHTOKEN=secret",
        "X-Requested-With": "XMLHttpRequest",
    }
    assert connection.sent == [
        {
            "seq": 1,
            "action": "authentication_challenge",
            "data": {"token": "secret"},
        }
    ]


def test_realtime_accepts_hello_as_header_authentication_confirmation():
    connection = FakeConnection(
        [
            {
                "event": "hello",
                "data": {"server_version": "7.8.0"},
                "broadcast": {"channel_id": ""},
                "seq": 0,
            }
        ]
    )
    client = RealtimeClient(
        "https://time.example.test",
        AuthContext("secret"),
        connector=lambda *_args, **_kwargs: connection,
    )
    event = next(client.iter_events(event_types={"hello"}, reconnect=False))
    assert event["event"] == "hello"
