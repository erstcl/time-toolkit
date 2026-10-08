from __future__ import annotations

import io
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from time_toolkit.auth import AuthContext
from time_toolkit.cli import build_parser
from time_toolkit.client import TimeClient
from time_toolkit.config import Profile
from time_toolkit.errors import PermissionError, TimeToolkitError, UsageError
from time_toolkit.output import emit
from time_toolkit.service import TimeService


def channel(identifier, **extra):
    return {
        "id": identifier,
        "team_id": "team-id",
        "name": identifier,
        "display_name": identifier,
        "type": "O",
        **extra,
    }


class DirectoryClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def get_me(self):
        return {"id": "user-id", "username": "student"}

    def get_public_channels_page(self, team_id, *, page, per_page):
        self.calls.append((team_id, page, per_page))
        return self.rows

    def iter_my_channels(self, user_id, team_id, *, max_pages):
        assert (user_id, team_id, max_pages) == ("user-id", "team-id", 0)
        yield [channel("earlier-joined")]
        yield [channel("joined")]


def service(client):
    return TimeService(
        Profile("test", "https://time.example.test", team_id="team-id", write_policy="readonly"),
        client,
    )


def test_discovery_filters_membership_from_all_pages_and_invalid_candidates():
    client = DirectoryClient(
        [
            channel("joined"),
            channel("new", purpose="NLP seminar"),
            channel("new", purpose="NLP seminar"),
            channel("deleted", delete_at=12),
            channel("private", type="P"),
            channel("wrong-team", team_id="other-team"),
            channel("unrelated"),
        ]
    )
    result = service(client).discover_channels(pattern="nlp")
    assert [row.id for row in result["channels"]] == ["new"]
    assert result["scanned_count"] == 7
    assert result["next_page"] is None
    assert client.calls == [("team-id", 0, 100)]


def test_empty_filtered_page_still_returns_continuation():
    result = service(DirectoryClient([channel("joined")])).discover_channels(page=4, per_page=1)
    assert result["channels"] == []
    assert result["next_page"] == 5
    assert result["page"] == 4


def test_include_joined_does_not_need_membership_requests():
    client = DirectoryClient([channel("joined")])
    client.iter_my_channels = lambda *_args, **_kwargs: pytest.fail("unexpected membership request")
    result = service(client).discover_channels(include_joined=True)
    assert [row.id for row in result["channels"]] == ["joined"]
    assert result["include_joined"] is True


@pytest.mark.parametrize("page,per_page", [(-1, 100), (0, 0), (0, 201)])
def test_discovery_rejects_invalid_pagination_before_requests(page, per_page):
    client = DirectoryClient([])
    with pytest.raises(UsageError):
        service(client).discover_channels(page=page, per_page=per_page)
    assert client.calls == []


def test_directory_client_uses_only_get_with_exact_pagination():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v4/teams/team-id/channels"
        assert dict(request.url.params) == {"page": "2", "per_page": "75"}
        return httpx.Response(200, json=[channel("new")])

    with httpx.Client(
        base_url="https://time.example.test", transport=httpx.MockTransport(handler)
    ) as http:
        client = TimeClient("https://time.example.test", AuthContext("synthetic"), http_client=http)
        assert client.get_public_channels_page("team-id", page=2, per_page=75)[0]["id"] == "new"


@pytest.mark.parametrize("payload", [{"channels": []}, ["invalid"]])
def test_invalid_directory_response_is_not_silently_reported_as_empty(payload):
    with httpx.Client(
        base_url="https://time.example.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
    ) as http:
        client = TimeClient("https://time.example.test", http_client=http)
        with pytest.raises(TimeToolkitError, match="invalid public-channel"):
            client.get_public_channels_page("team-id")


def test_directory_permission_error_is_propagated():
    with httpx.Client(
        base_url="https://time.example.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(403, json={})),
    ) as http:
        client = TimeClient("https://time.example.test", http_client=http)
        with pytest.raises(PermissionError):
            client.get_public_channels_page("team-id")


def test_cli_discovery_preserves_profile_server_and_continuation(monkeypatch):
    client = DirectoryClient([channel("new")])
    monkeypatch.setattr(TimeService, "open", lambda *_args, **_kwargs: service(client))
    client.close = lambda: None
    stream = io.StringIO()
    monkeypatch.setattr(
        "time_toolkit.cli.emit", lambda data, **kwargs: emit(data, stream=stream, **kwargs)
    )
    args = build_parser().parse_args(
        ["-p", "test", "-o", "json", "discover-channels", "--page", "2", "--per-page", "1"]
    )
    assert args.func(args) == 0
    result = json.loads(stream.getvalue())
    assert result["profile"] == "test"
    assert result["server"] == "https://time.example.test"
    assert result["data"]["channels"][0]["id"] == "new"
    assert result["data"]["next_page"] == 3


def test_mcp_discovery_is_read_only_and_preserves_pagination(monkeypatch):
    pytest.importorskip("mcp")
    import time_toolkit.mcp_server as server

    observed = {}

    @contextmanager
    def fake_service(profile):
        observed["profile"] = profile

        def discover(**kwargs):
            observed.update(kwargs)
            return {"channels": [], "next_page": 2}

        yield SimpleNamespace(
            profile=SimpleNamespace(base_url="https://time.example.test"),
            discover_channels=discover,
        )

    monkeypatch.setattr(server, "_service", fake_service)
    result = server.time_discover_channels("test", pattern="nlp", page=1, per_page=20)
    assert observed == {
        "profile": "test",
        "pattern": "nlp",
        "page": 1,
        "per_page": 20,
        "include_joined": False,
    }
    assert result["data"]["next_page"] == 2
    assert result["profile"] == "test"
    tools = {tool.name: tool for tool in server.mcp._tool_manager.list_tools()}
    assert tools["time_discover_channels"].annotations.readOnlyHint is True
    assert tools["time_discover_channels"].annotations.destructiveHint is False
