from __future__ import annotations

from typing import Any

import pytest

from time_toolkit.config import Profile
from time_toolkit.errors import ConflictError, NotFoundError
from time_toolkit.service import TimeService

USER_ID = "u" * 26
TEAM_ID = "t" * 26
CHANNEL_A = "a" * 26
CHANNEL_B = "b" * 26
CHANNEL_C = "c" * 26
MISSING_CHANNEL = "d" * 26
CUSTOM_CATEGORY = "m" * 26
FAVORITES_CATEGORY = "f" * 26
CHANNELS_CATEGORY = "h" * 26
DIRECT_CATEGORY = "r" * 26


def category_payload() -> dict[str, Any]:
    return {
        "categories": [
            {
                "id": CHANNELS_CATEGORY,
                "user_id": USER_ID,
                "team_id": TEAM_ID,
                "type": "channels",
                "display_name": "Channels",
                "sorting": "alphabetical",
                "muted": False,
                "collapsed": False,
                "channel_ids": [CHANNEL_A],
            },
            {
                "id": CUSTOM_CATEGORY,
                "user_id": USER_ID,
                "team_id": TEAM_ID,
                "type": "custom",
                "display_name": "Study",
                "sorting": "manual",
                "muted": True,
                "collapsed": "true",
                "channel_ids": [CHANNEL_B, CHANNEL_C, CHANNEL_A, MISSING_CHANNEL],
            },
            {
                "id": DIRECT_CATEGORY,
                "user_id": USER_ID,
                "team_id": TEAM_ID,
                "type": "direct_messages",
                "display_name": "Direct Messages",
                "sorting": "recent",
                "muted": False,
                "collapsed": False,
                "channel_ids": [],
            },
            {
                "id": FAVORITES_CATEGORY,
                "user_id": USER_ID,
                "team_id": TEAM_ID,
                "type": "favorites",
                "display_name": "Favorites",
                "sorting": "manual",
                "muted": False,
                "collapsed": False,
                "channel_ids": [CHANNEL_B],
            },
        ],
        "order": [
            FAVORITES_CATEGORY,
            CUSTOM_CATEGORY,
            CHANNELS_CATEGORY,
            DIRECT_CATEGORY,
        ],
    }


class FakeCategoryClient:
    base_url = "https://time.example.test"

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or category_payload()
        self.calls: list[tuple[str, ...]] = []

    def get_me(self) -> dict[str, Any]:
        self.calls.append(("get_me",))
        return {"id": USER_ID, "username": "student"}

    def get_sidebar_categories(self, user_id: str, team_id: str) -> dict[str, Any]:
        self.calls.append(("get_sidebar_categories", user_id, team_id))
        return self.payload

    def iter_my_channels(self, user_id: str, team_id: str, **_: Any):
        self.calls.append(("iter_my_channels", user_id, team_id))
        yield [
            {
                "id": CHANNEL_A,
                "name": "channel-a",
                "display_name": "Channel A",
                "type": "O",
                "team_id": TEAM_ID,
            },
            {
                "id": CHANNEL_B,
                "name": "channel-b",
                "display_name": "Channel B",
                "type": "O",
                "team_id": TEAM_ID,
            },
        ]

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        self.calls.append(("get_channel", channel_id))
        if channel_id == CHANNEL_C:
            return {
                "id": CHANNEL_C,
                "name": "channel-c",
                "display_name": "Channel C",
                "type": "P",
                "team_id": TEAM_ID,
            }
        raise NotFoundError("Synthetic channel is unavailable")

    def get_users_by_ids(self, _: list[str]) -> list[dict[str, Any]]:
        self.calls.append(("get_users_by_ids",))
        return []


def service(client: FakeCategoryClient | None = None) -> TimeService:
    return TimeService(
        Profile("example", "https://time.example.test", team_id=TEAM_ID),
        client or FakeCategoryClient(),  # type: ignore[arg-type]
    )


def test_sidebar_categories_preserve_server_order_and_all_standard_types():
    client = FakeCategoryClient()
    categories = service(client).sidebar_categories()
    assert [category.id for category in categories] == [
        FAVORITES_CATEGORY,
        CUSTOM_CATEGORY,
        CHANNELS_CATEGORY,
        DIRECT_CATEGORY,
    ]
    assert [category.type for category in categories] == [
        "favorites",
        "custom",
        "channels",
        "direct_messages",
    ]
    custom = categories[1]
    assert custom.display_name == "Study"
    assert custom.sorting == "manual"
    assert custom.muted is True
    assert custom.collapsed is True
    assert custom.channel_ids == (CHANNEL_B, CHANNEL_C, CHANNEL_A, MISSING_CHANNEL)
    assert ("get_sidebar_categories", USER_ID, TEAM_ID) in client.calls


def test_sidebar_category_resolves_exact_id_and_display_name():
    time = service()
    assert time.resolve_sidebar_category(CUSTOM_CATEGORY).id == CUSTOM_CATEGORY
    assert time.resolve_sidebar_category("Study").id == CUSTOM_CATEGORY


def test_sidebar_category_rejects_ambiguous_or_unknown_names():
    payload = category_payload()
    payload["categories"].append(
        {
            "id": "x" * 26,
            "user_id": USER_ID,
            "team_id": TEAM_ID,
            "type": "custom",
            "display_name": "Study",
            "sorting": "manual",
            "channel_ids": [],
        }
    )
    time = service(FakeCategoryClient(payload))
    with pytest.raises(ConflictError) as error:
        time.resolve_sidebar_category("Study")
    assert [match["id"] for match in error.value.details["matches"]] == [
        CUSTOM_CATEGORY,
        "x" * 26,
    ]
    with pytest.raises(NotFoundError, match="not found"):
        time.resolve_sidebar_category("Unknown")


def test_category_channels_preserve_order_and_skip_unavailable_channels():
    client = FakeCategoryClient()
    channels = service(client).category_channels(CUSTOM_CATEGORY)
    assert [channel.id for channel in channels] == [CHANNEL_B, CHANNEL_C, CHANNEL_A]
    assert [call for call in client.calls if call[0] == "get_channel"] == [
        ("get_channel", CHANNEL_C),
        ("get_channel", MISSING_CHANNEL),
    ]
    assert {call[0] for call in client.calls} <= {
        "get_me",
        "get_sidebar_categories",
        "iter_my_channels",
        "get_channel",
        "get_users_by_ids",
    }
