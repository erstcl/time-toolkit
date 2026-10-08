from __future__ import annotations

import copy
import json

import httpx
import pytest

from time_toolkit.cli import build_parser
from time_toolkit.client import TimeClient
from time_toolkit.config import Profile
from time_toolkit.errors import ConflictError, PermissionError, UsageError
from time_toolkit.service import TimeService

USER, TEAM, CHANNEL, OTHER, TARGET = (letter * 26 for letter in "utabc")


def folder(identifier, ids, **extras):
    return {
        "id": identifier,
        "user_id": USER,
        "team_id": TEAM,
        "type": "custom",
        "display_name": identifier,
        "sorting": "manual",
        "muted": False,
        "collapsed": False,
        "channel_ids": ids,
        **extras,
    }


class Client:
    def __init__(self):
        self.members = []
        self.channel = {"id": CHANNEL, "team_id": TEAM, "type": "O", "name": "study"}
        self.categories = [
            folder(TARGET, [OTHER]),
            folder("source", [CHANNEL], extension={"kept": True}),
        ]
        self.writes = []

    def get_me(self):
        return {"id": USER, "username": "student"}

    def get_channel(self, _identifier):
        return self.channel

    def iter_my_channels(self, _user, _team, **_kwargs):
        yield [{"id": identifier} for identifier in self.members]

    def get_sidebar_categories(self, _user, _team):
        return {"categories": self.categories, "order": [TARGET, "source"]}

    def update_sidebar_categories(self, user, team, categories, **_kwargs):
        self.writes.append(("move", user, team, categories))

    def join_channel(self, user, channel, **_kwargs):
        self.writes.append(("join", user, channel))
        return {"user_id": user, "channel_id": channel}

    def create_sidebar_category(self, user, team, name, **_kwargs):
        self.writes.append(("create", user, team, name))
        return folder("created", [], display_name=name)


def service(client, policy="approval", mode="confirmed"):
    return TimeService(
        Profile("test", "https://time.example.test", team_id=TEAM, write_policy=policy),
        client,
        write_mode=mode,
    )


def test_join_only_adds_current_account_and_is_noop_for_members():
    client = Client()
    result = service(client).join_channel(CHANNEL)
    assert result["already_joined"] is False
    assert client.writes == [("join", USER, CHANNEL)]
    client.members.append(CHANNEL)
    client.writes.clear()
    assert service(client).join_channel(CHANNEL)["already_joined"] is True
    assert client.writes == []


@pytest.mark.parametrize("overrides", [{"type": "P"}, {"delete_at": 1}, {"team_id": "elsewhere"}])
def test_join_rejects_nonpublic_archived_and_other_team_channels(overrides):
    client = Client()
    client.channel.update(overrides)
    with pytest.raises(UsageError):
        service(client).join_channel(CHANNEL)
    assert client.writes == []


@pytest.mark.parametrize("operation", ["join", "create", "move"])
@pytest.mark.parametrize("policy,mode", [("readonly", "confirmed"), ("approval", "automated")])
def test_write_policy_is_enforced_before_management_requests(operation, policy, mode):
    client = Client()
    active = service(client, policy, mode)
    with pytest.raises(PermissionError):
        if operation == "join":
            active.join_channel(CHANNEL)
        elif operation == "create":
            active.create_category("New channels")
        else:
            active.move_channels_to_category([CHANNEL], TARGET)
    assert client.writes == []


def test_create_category_rejects_existing_exact_name():
    client = Client()
    assert service(client).create_category("New channels").display_name == "New channels"
    client.writes.clear()
    with pytest.raises(ConflictError):
        service(client).create_category(TARGET)
    assert client.writes == []


def test_move_preserves_unrelated_settings_order_and_input_snapshot():
    client = Client()
    client.members = [CHANNEL, OTHER]
    original = copy.deepcopy(client.categories)
    result = service(client).move_channels_to_category([CHANNEL, CHANNEL], TARGET)
    assert result["channel_ids"] == [CHANNEL]
    assert result["changed"] is True
    sent = client.writes[0][3]
    assert sent[0]["channel_ids"] == [OTHER, CHANNEL]
    assert sent[1]["channel_ids"] == []
    assert sent[1]["extension"] == {"kept": True}
    assert client.categories == original
    client.categories = sent
    client.writes.clear()
    assert service(client).move_channels_to_category([CHANNEL], TARGET)["changed"] is False
    assert client.writes == []


def test_move_rejects_nonmember_channels_and_foreign_owner():
    client = Client()
    with pytest.raises(UsageError, match="already belong"):
        service(client).move_channels_to_category([CHANNEL], TARGET)
    client.members = [CHANNEL]
    client.categories[1]["user_id"] = "someone-else"
    with pytest.raises(ConflictError, match="ownership"):
        service(client).move_channels_to_category([CHANNEL], TARGET)
    assert client.writes == []


def test_management_client_payloads_use_exact_endpoints_and_owner():
    calls = []

    def handle(request):
        body = json.loads(request.content)
        calls.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"id": "created"})

    with httpx.Client(
        base_url="https://time.example.test", transport=httpx.MockTransport(handle)
    ) as http:
        client = TimeClient("https://time.example.test", http_client=http)
        client.join_channel(USER, CHANNEL, idempotency_key="join")
        client.create_sidebar_category(USER, TEAM, "New channels", idempotency_key="create")
        client.update_sidebar_categories(
            USER, TEAM, [folder(TARGET, [CHANNEL])], idempotency_key="move"
        )
    assert calls[0] == ("POST", f"/api/v4/channels/{CHANNEL}/members", {"user_id": USER})
    assert calls[1][2]["type"] == "custom"
    assert calls[1][2]["display_name"] == "New channels"
    assert calls[2][0] == "PUT"
    assert calls[2][2][0]["channel_ids"] == [CHANNEL]


def test_cli_management_preview_and_mcp_confirmation_contract():
    args = build_parser().parse_args(["-p", "test", "move-channel", CHANNEL, TARGET, "--dry-run"])
    assert args.category == TARGET and args.dry_run is True
    pytest.importorskip("mcp")
    from time_toolkit.mcp_server import PreparedWriteStore, _execute_write

    client = Client()
    client.members = [CHANNEL]
    store = PreparedWriteStore()
    with pytest.raises(UsageError, match="explicit category"):
        store.prepare(
            profile="test",
            server="https://time.example.test",
            action="move-channel",
            target=CHANNEL,
        )
    item = store.prepare(
        profile="test",
        server="https://time.example.test",
        action="move-channel",
        target=CHANNEL,
        category=TARGET,
    )
    assert item.preview()["category"] == TARGET
    with pytest.raises(UsageError):
        store.claim(item.id, "yes")
    claimed = store.claim(item.id, item.confirmation)
    assert _execute_write(service(client), claimed)["changed"] is True
    with pytest.raises(UsageError):
        store.claim(item.id, item.confirmation)
