from __future__ import annotations

import json
import ssl

import pytest

pytest.importorskip("websockets")

from time_toolkit.auth import AuthContext
from time_toolkit.config import Profile
from time_toolkit.errors import AuthenticationError, UsageError
from time_toolkit.models import Channel, RealtimeEvent, primitive
from time_toolkit.realtime import (
    RealtimeClient,
    RealtimeService,
    normalize_event,
    websocket_ssl_context,
    websocket_url,
)


class FakeConnection:
    def __init__(
        self,
        messages: list[dict[str, object]],
        *,
        disconnect_error: Exception | None = None,
    ) -> None:
        self.messages = iter(json.dumps(message) for message in messages)
        self.sent: list[dict[str, object]] = []
        self.disconnect_error = disconnect_error
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_: object):
        self.closed = True
        return None

    def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    def recv(self, timeout: float | None = None) -> str:
        del timeout
        try:
            return next(self.messages)
        except StopIteration:
            if self.disconnect_error is not None:
                raise self.disconnect_error from None
            raise


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


def test_realtime_event_preserves_data_and_builds_typed_post():
    payload = normalize_event(
        json.dumps(
            {
                "event": "posted",
                "data": {
                    "post": json.dumps(
                        {
                            "id": "post-id",
                            "channel_id": "channel-id",
                            "user_id": "user-id",
                            "message": "Synthetic notification",
                            "create_at": 10,
                            "update_at": 11,
                            "edit_at": 12,
                            "type": "custom_post_type",
                            "props": {"from_bot": True, "future_field": {"enabled": True}},
                        }
                    ),
                    "unknown_data": {"nested": "kept"},
                },
                "broadcast": {"channel_id": "channel-id", "future_flag": True},
                "seq": 4,
            }
        )
    )
    event = RealtimeEvent.from_api(payload, base_url="https://time.example.test")
    serialized = primitive(event)
    assert event.event == "posted"
    assert event.channel_id == "channel-id"
    assert event.post_id == "post-id"
    assert event.post is not None
    assert event.post.post_type == "custom_post_type"
    assert event.post.edit_at == 12
    assert event.post.is_from_bot is True
    assert event.data["unknown_data"] == {"nested": "kept"}
    assert event.data["post"]["props"]["future_field"] == {"enabled": True}
    assert event.broadcast["future_flag"] is True
    assert serialized["semantic_key"].startswith("rt1:")


def test_semantic_key_ignores_seq_and_tracks_post_revisions():
    def event(event_type: str, *, seq: int, update_at: int, edit_at: int = 0):
        return RealtimeEvent.from_api(
            {
                "event": event_type,
                "data": {
                    "post": {
                        "id": "post-id",
                        "channel_id": "channel-id",
                        "create_at": 10,
                        "update_at": update_at,
                        "edit_at": edit_at,
                    }
                },
                "seq": seq,
            },
            base_url="https://time.example.test",
        )

    first = event("posted", seq=2, update_at=10)
    replay = event("posted", seq=99, update_at=10)
    edited = event("post_edited", seq=100, update_at=20, edit_at=20)
    next_edit = event("post_edited", seq=101, update_at=30, edit_at=30)
    assert first.semantic_key == replay.semantic_key
    assert edited.semantic_key != next_edit.semantic_key
    assert first.semantic_key != edited.semantic_key


def test_semantic_key_handles_deleted_reactions_hello_and_unknown_events():
    deleted = RealtimeEvent.from_api(
        {
            "event": "post_deleted",
            "data": {"post": {"id": "post-id", "delete_at": 40}},
            "seq": 1,
        },
        base_url="https://time.example.test",
    )
    first_reaction = RealtimeEvent.from_api(
        {
            "event": "reaction_added",
            "data": {
                "reaction": {
                    "post_id": "post-id",
                    "user_id": "user-id",
                    "emoji_name": "thumbsup",
                    "create_at": 50,
                }
            },
            "seq": 2,
        },
        base_url="https://time.example.test",
    )
    second_reaction = RealtimeEvent.from_api(
        {
            "event": "reaction_added",
            "data": {
                "reaction": {
                    "post_id": "post-id",
                    "user_id": "user-id",
                    "emoji_name": "thumbsup",
                    "create_at": 60,
                }
            },
            "seq": 3,
        },
        base_url="https://time.example.test",
    )
    hello_a = RealtimeEvent.from_api(
        {
            "event": "hello",
            "data": {
                "connection_id": "connection-a",
                "server_type": "local",
                "server_version": "1.0",
            },
            "seq": 0,
        },
        base_url="https://time.example.test",
    )
    hello_b = RealtimeEvent.from_api(
        {
            "event": "hello",
            "data": {
                "connection_id": "connection-b",
                "server_type": "local",
                "server_version": "1.0",
            },
            "seq": 20,
        },
        base_url="https://time.example.test",
    )
    unknown_a = RealtimeEvent.from_api(
        {"event": "future_event", "data": {"value": 1}, "seq": 5},
        base_url="https://time.example.test",
    )
    unknown_b = RealtimeEvent.from_api(
        {"event": "future_event", "data": {"value": 1}, "seq": 6},
        base_url="https://time.example.test",
    )
    assert deleted.post_id == "post-id"
    assert first_reaction.semantic_key != second_reaction.semantic_key
    assert hello_a.semantic_key == hello_b.semantic_key
    assert unknown_a.semantic_key == unknown_b.semantic_key


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
    assert event.data["post"]["id"] == "post-id"
    assert event.post is not None
    assert event.post.id == "post-id"
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
    assert event.event == "hello"


def test_reconnect_deduplicates_same_event_with_a_new_seq(monkeypatch):
    post = {
        "id": "post-id",
        "channel_id": "channel-id",
        "message": "Synthetic message",
        "create_at": 10,
        "update_at": 10,
    }
    first = FakeConnection(
        [
            {"status": "OK", "seq_reply": 1},
            {"event": "posted", "data": {"post": post}, "seq": 2},
        ],
        disconnect_error=OSError("synthetic disconnect"),
    )
    second = FakeConnection(
        [
            {"status": "OK", "seq_reply": 1},
            {"event": "posted", "data": {"post": post}, "seq": 99},
            {
                "event": "post_edited",
                "data": {"post": {**post, "update_at": 20, "edit_at": 20}},
                "seq": 100,
            },
        ]
    )
    connections = iter([first, second])
    monkeypatch.setattr("time_toolkit.realtime.time.sleep", lambda _delay: None)
    client = RealtimeClient(
        "https://time.example.test",
        AuthContext("secret"),
        connector=lambda *_args, **_kwargs: next(connections),
    )
    events = client.iter_events(
        event_types={"posted", "post_edited"}, reconnect=True, max_reconnects=1
    )
    assert next(events).event == "posted"
    assert next(events).event == "post_edited"
    events.close()


def test_authentication_rejection_does_not_reconnect():
    calls = 0

    def connector(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeConnection([{"status": "FAIL", "seq_reply": 1}])

    client = RealtimeClient(
        "https://time.example.test",
        AuthContext("secret"),
        connector=connector,
    )
    with pytest.raises(AuthenticationError, match="rejected"):
        next(client.iter_events(reconnect=True))
    assert calls == 1


def test_realtime_service_uses_explicit_profile_and_closes_active_stream(monkeypatch):
    selected: dict[str, object] = {}
    connection = FakeConnection(
        [
            {"status": "OK", "seq_reply": 1},
            {
                "event": "hello",
                "data": {"server_type": "test", "server_version": "1"},
                "seq": 0,
            },
        ]
    )

    class FakeHttpClient:
        auth = AuthContext("secret")

        @staticmethod
        def get_websocket_url():
            return "wss://socket.example.test"

    class FakeTimeService:
        profile = Profile(
            "selected",
            "https://time.example.test",
            allowed_websocket_hosts=("socket.example.test",),
        )
        client = FakeHttpClient()
        closed = False

        @classmethod
        def open(cls, profile_name, *, config=None, secrets=None):
            selected.update(profile=profile_name, config=config, secrets=secrets)
            return cls()

        @staticmethod
        def resolve_channel(value):
            return Channel("channel-id", value, value, "O")

        def close(self):
            self.closed = True

    config = object()
    secrets = object()
    monkeypatch.setattr("time_toolkit.realtime.TimeService", FakeTimeService)
    with RealtimeService.open(
        "selected",
        config=config,
        secrets=secrets,
        connector=lambda *_args, **_kwargs: connection,
    ) as service:
        assert service.profile.name == "selected"
        assert service.server == "https://time.example.test"
        assert service.resolve_channel("general").id == "channel-id"
        events = service.iter_events(event_types={"hello"}, reconnect=False)
        assert next(events).event == "hello"
    assert selected == {"profile": "selected", "config": config, "secrets": secrets}
    assert connection.closed is True
    assert service._time_service.closed is True


def test_realtime_service_rejects_an_implicit_profile():
    with pytest.raises(UsageError, match="explicit profile"):
        RealtimeService.open("")
