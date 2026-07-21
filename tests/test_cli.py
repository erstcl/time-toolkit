from __future__ import annotations

import argparse
import io
import json

import pytest

from time_toolkit.cli import _require_profile, _write_mode, build_parser, cmd_watch
from time_toolkit.config import Profile
from time_toolkit.errors import UsageError
from time_toolkit.models import Channel, RealtimeConnection, RealtimeEvent, SidebarCategory
from time_toolkit.output import emit
from time_toolkit.realtime import RealtimeService
from time_toolkit.service import TimeService


def test_parser_uses_time_toolkit_command_name():
    assert build_parser().prog == "timetk"


def test_write_parser_keeps_explicit_profile_and_safety_flags():
    args = build_parser().parse_args(
        ["--profile", "example", "post", "general", "--message", "hello", "--dry-run"]
    )
    assert _require_profile(args, explicit=True) == "example"
    assert args.dry_run is True
    assert args.yes is False
    assert _write_mode(args) == "confirmed"


def test_write_rejects_implicit_default_profile():
    args = argparse.Namespace(profile=None)
    with pytest.raises(UsageError, match="explicit --profile"):
        _require_profile(args, explicit=True)


def test_yes_selects_automated_write_mode():
    args = build_parser().parse_args(
        ["--profile", "example", "post", "general", "--message", "hello", "--yes"]
    )
    assert _write_mode(args) == "automated"


def test_profile_parser_exposes_named_write_policies():
    added = build_parser().parse_args(["profile", "add", "example", "https://time.example.test"])
    updated = build_parser().parse_args(
        ["profile", "update", "example", "--write-policy", "fullauto"]
    )
    assert added.write_policy == "approval"
    assert updated.write_policy == "fullauto"


def test_profile_parser_collects_explicit_websocket_hosts():
    args = build_parser().parse_args(
        [
            "profile",
            "update",
            "example",
            "--websocket-host",
            "socket.example.test",
            "--websocket-host",
            "events.example.test:8443",
        ]
    )
    assert args.websocket_hosts == ["socket.example.test", "events.example.test:8443"]


@pytest.mark.parametrize(
    ("command", "expected_id"),
    [
        (["categories"], "category-id"),
        (["category-channels", "Study"], "channel-id"),
    ],
)
def test_sidebar_category_commands_use_public_service(monkeypatch, command, expected_id):
    selected: dict[str, object] = {}
    stream = io.StringIO()

    class FakeTimeService:
        profile = Profile("selected", "https://time.example.test")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def sidebar_categories():
            return [
                SidebarCategory(
                    id="category-id",
                    user_id="user-id",
                    team_id="team-id",
                    type="custom",
                    display_name="Study",
                    sorting="manual",
                    muted=False,
                    collapsed=False,
                    channel_ids=("channel-id",),
                )
            ]

        @staticmethod
        def category_channels(value):
            selected["category"] = value
            return [Channel("channel-id", "study", "Study", "O")]

    def open_service(cls, profile_name, **_kwargs):
        del cls
        selected["profile"] = profile_name
        return FakeTimeService()

    monkeypatch.setattr(TimeService, "open", classmethod(open_service))
    monkeypatch.setattr(
        "time_toolkit.cli.emit", lambda data, **kwargs: emit(data, stream=stream, **kwargs)
    )
    args = build_parser().parse_args(["--profile", "selected", "--format", "json", *command])
    assert args.func(args) == 0
    payload = json.loads(stream.getvalue())
    assert payload["profile"] == "selected"
    assert payload["server"] == "https://time.example.test"
    assert payload["data"][0]["id"] == expected_id
    assert selected["profile"] == "selected"
    if command[0] == "category-channels":
        assert selected["category"] == "Study"


@pytest.mark.parametrize("lifecycle", [False, True])
def test_watch_keeps_ndjson_envelope_and_uses_public_realtime_service(monkeypatch, lifecycle):
    selected: dict[str, object] = {}
    stream = io.StringIO()

    class FakeRealtimeService:
        profile = Profile("selected", "https://time.example.test")
        server = profile.base_url

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def resolve_channel(value):
            raise AssertionError(f"Unexpected channel lookup: {value}")

        @staticmethod
        def iter_events(**kwargs):
            selected["iter_events"] = kwargs
            on_connected = kwargs.get("on_connected")
            if on_connected is not None:
                on_connected(
                    RealtimeConnection(
                        state="connected",
                        reconnected=False,
                        connection_id="connection-id",
                    )
                )
            yield RealtimeEvent.from_api(
                {
                    "event": "posted",
                    "data": {
                        "post": {
                            "id": "post-id",
                            "channel_id": "channel-id",
                            "message": "Synthetic message",
                            "create_at": 10,
                        }
                    },
                    "broadcast": {"channel_id": "channel-id"},
                    "seq": 2,
                },
                base_url="https://time.example.test",
            )

    def open_service(cls, profile_name, *, config=None, **_kwargs):
        del cls
        selected["profile"] = profile_name
        selected["config_type"] = type(config).__name__
        return FakeRealtimeService()

    monkeypatch.setattr(RealtimeService, "open", classmethod(open_service))
    monkeypatch.setattr(
        "time_toolkit.cli.emit", lambda data, **kwargs: emit(data, stream=stream, **kwargs)
    )
    argv = ["--profile", "selected", "--format", "ndjson", "watch", "--once"]
    if lifecycle:
        argv.append("--lifecycle")
    args = build_parser().parse_args(argv)
    assert cmd_watch(args) == 0
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    payload = records[-1]
    if lifecycle:
        connection = records[0]
        assert connection["meta"] == {"stream": "websocket", "kind": "lifecycle"}
        assert connection["data"] == {
            "state": "connected",
            "reconnected": False,
            "connection_id": "connection-id",
        }
    else:
        assert len(records) == 1
    assert payload["schema_version"] == "1.0"
    assert payload["profile"] == "selected"
    assert payload["server"] == "https://time.example.test"
    assert payload["data"]["event"] == "posted"
    assert payload["data"]["data"]["post"]["id"] == "post-id"
    assert payload["data"]["broadcast"]["channel_id"] == "channel-id"
    assert payload["data"]["seq"] == 2
    assert payload["data"]["semantic_key"].startswith("rt1:")
    assert selected["profile"] == "selected"
    assert selected["config_type"] == "ConfigStore"
    iter_options = selected["iter_events"]
    on_connected = iter_options.pop("on_connected", None)
    assert callable(on_connected) is lifecycle
    assert iter_options == {
        "event_types": {"posted"},
        "channel_ids": set(),
        "reconnect": True,
        "max_reconnects": 0,
    }
