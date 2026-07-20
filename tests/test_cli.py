from __future__ import annotations

import argparse

import pytest

from time_toolkit.cli import _require_profile, _write_mode, build_parser
from time_toolkit.errors import UsageError


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
