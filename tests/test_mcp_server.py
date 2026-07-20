from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp.exceptions import ToolError

import time_toolkit.mcp_server as mcp_server
from time_toolkit.errors import UsageError
from time_toolkit.mcp_server import PreparedWriteStore, mcp


def test_mcp_write_tools_enforce_prepare_then_destructive_commit():
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    assert tools["time_prepare_write"].annotations.readOnlyHint is True
    assert tools["time_prepare_write"].annotations.destructiveHint is False
    assert tools["time_commit_write"].annotations.readOnlyHint is False
    assert tools["time_commit_write"].annotations.destructiveHint is True
    assert set(tools["time_prepare_write"].parameters["required"]) == {
        "profile",
        "action",
        "target",
    }


def test_prepared_write_confirmation_is_exact_and_one_time():
    store = PreparedWriteStore(ttl_seconds=600)
    item = store.prepare(
        profile="example",
        server="https://time.example.test",
        action="post",
        target="general",
        message="hello",
    )
    with pytest.raises(UsageError, match="does not match"):
        store.claim(item.id, "yes")
    assert store.claim(item.id, item.confirmation) == item
    with pytest.raises(UsageError, match="not found"):
        store.claim(item.id, item.confirmation)


def test_prepared_write_validates_action_payload():
    store = PreparedWriteStore()
    with pytest.raises(UsageError, match="message or file IDs"):
        store.prepare(
            profile="example",
            server="https://time.example.test",
            action="post",
            target="general",
        )
    with pytest.raises(UsageError, match="emoji"):
        store.prepare(
            profile="example",
            server="https://time.example.test",
            action="react",
            target="p" * 26,
        )


def test_mcp_commit_uses_confirmed_write_mode(monkeypatch):
    store = PreparedWriteStore()
    item = store.prepare(
        profile="example",
        server="https://time.example.test",
        action="delete",
        target="p" * 26,
    )
    observed = {}

    @contextmanager
    def fake_service(profile, *, write_mode="automated"):
        observed.update(profile=profile, write_mode=write_mode)
        yield SimpleNamespace(profile=SimpleNamespace(base_url="https://time.example.test"))

    monkeypatch.setattr(mcp_server, "_prepared_writes", store)
    monkeypatch.setattr(mcp_server, "_service", fake_service)
    monkeypatch.setattr(mcp_server, "_execute_write", lambda service, prepared: {"ok": True})

    result = mcp_server.time_commit_write(item.id, item.confirmation)

    assert observed == {"profile": "example", "write_mode": "confirmed"}
    assert result["data"] == {"ok": True}


def test_readonly_profile_is_rejected_before_mcp_preparation(monkeypatch):
    profile = SimpleNamespace(
        name="archive",
        base_url="https://time.example.test",
        mcp_enabled=True,
        write_policy="readonly",
    )
    monkeypatch.setattr(
        mcp_server,
        "ConfigStore",
        lambda: SimpleNamespace(get_profile=lambda _name: profile),
    )

    with pytest.raises(ToolError, match="Writes are disabled"):
        mcp_server.time_prepare_write("archive", "delete", "p" * 26)
