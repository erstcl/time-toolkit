from __future__ import annotations

from typing import Any

import pytest

from time_toolkit.config import Profile
from time_toolkit.errors import ConflictError, PermissionError
from time_toolkit.service import TimeService, extract_post_id


class FakeClient:
    base_url = "https://time.example.test"

    def get_me(self):
        return {"id": "u" * 26, "username": "student"}

    def get_my_teams(self):
        return [{"id": "t" * 26, "name": "main", "display_name": "Main"}]

    def iter_my_channels(self, *_: Any, **__: Any):
        yield [
            {
                "id": "a" * 26,
                "name": "math-homework",
                "display_name": "Math Homework",
                "type": "O",
                "team_id": "t" * 26,
            },
            {
                "id": "b" * 26,
                "name": "math-general",
                "display_name": "Math General",
                "type": "O",
                "team_id": "t" * 26,
            },
        ]

    def get_channel_by_name(self, *_: Any):
        from time_toolkit.errors import NotFoundError

        raise NotFoundError("missing")

    def get_users_by_ids(self, _: list[str]):
        return []

    def delete_post(self, post_id: str, *, idempotency_key: str):
        self.deleted = (post_id, idempotency_key)

    def close(self):
        return None


def test_ambiguous_channel_is_never_selected_silently():
    service = TimeService(Profile("example", "https://time.example.test"), FakeClient())  # type: ignore[arg-type]
    with pytest.raises(ConflictError) as error:
        service.resolve_channel("math")
    assert error.value.details["matches"] == ["Math Homework", "Math General"]


def test_post_id_can_be_extracted_from_time_links():
    post_id = "p" * 26
    assert extract_post_id(post_id) == post_id
    assert extract_post_id(f"https://time.example.test/_redirect/pl/{post_id}") == post_id
    assert extract_post_id(f"https://time.example.test/x/thread/{post_id}") == post_id


def test_automated_writes_require_fullauto_by_default():
    service = TimeService(Profile("example", "https://time.example.test"), FakeClient())  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="--write-policy fullauto"):
        service.delete_post("p" * 26)


def test_fullauto_allows_automated_writes():
    client = FakeClient()
    service = TimeService(
        Profile(
            "automatic",
            "https://time.example.test",
            write_policy="fullauto",
        ),
        client,  # type: ignore[arg-type]
    )
    result = service.delete_post("p" * 26)
    assert result == {"deleted": True, "post_id": "p" * 26}
    assert client.deleted[0] == "p" * 26


def test_readonly_blocks_even_confirmed_writes():
    service = TimeService(
        Profile("archive", "https://time.example.test", write_policy="readonly"),
        FakeClient(),  # type: ignore[arg-type]
        write_mode="confirmed",
    )
    with pytest.raises(PermissionError, match="Writes are disabled"):
        service.delete_post("p" * 26)


def test_approval_allows_confirmed_write():
    client = FakeClient()
    service = TimeService(
        Profile("manual", "https://time.example.test", write_policy="approval"),
        client,  # type: ignore[arg-type]
        write_mode="confirmed",
    )
    service.delete_post("p" * 26)
    assert client.deleted[0] == "p" * 26
