from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from time_toolkit.dates import iso_utc


@dataclass(frozen=True, slots=True)
class User:
    id: str
    username: str
    first_name: str = ""
    last_name: str = ""
    nickname: str = ""
    email: str = ""
    position: str = ""

    @property
    def display_name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part).strip()

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> User:
        return cls(
            id=str(raw.get("id", "")),
            username=str(raw.get("username", "")),
            first_name=str(raw.get("first_name", "")),
            last_name=str(raw.get("last_name", "")),
            nickname=str(raw.get("nickname", "")),
            email=str(raw.get("email", "")),
            position=str(raw.get("position", "")),
        )


@dataclass(frozen=True, slots=True)
class Team:
    id: str
    name: str
    display_name: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Team:
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            display_name=str(raw.get("display_name", "")),
        )


@dataclass(frozen=True, slots=True)
class Channel:
    id: str
    name: str
    display_name: str
    type: str
    team_id: str = ""
    total_msg_count: int = 0
    label: str = ""

    @classmethod
    def from_api(cls, raw: dict[str, Any], *, label: str = "") -> Channel:
        return cls(
            id=str(raw.get("id", "")),
            name=str(raw.get("name", "")),
            display_name=str(raw.get("display_name", "")),
            type=str(raw.get("type", "")),
            team_id=str(raw.get("team_id", "")),
            total_msg_count=int(raw.get("total_msg_count", 0) or 0),
            label=label or str(raw.get("display_name") or raw.get("name") or raw.get("id", "")),
        )


def _contains_mention(message: str, username: str) -> bool:
    if not username:
        return False
    targets = (re.escape(username), "all", "channel", "here")
    return any(re.search(rf"(?<![\w@])@{target}\b", message, re.IGNORECASE) for target in targets)


@dataclass(frozen=True, slots=True)
class Post:
    id: str
    channel_id: str
    user_id: str
    message: str
    create_at: int
    update_at: int = 0
    delete_at: int = 0
    root_id: str = ""
    author: str = ""
    reply_count: int = 0
    is_pinned: bool = False
    is_mention: bool = False
    file_ids: tuple[str, ...] = ()
    permalink: str = ""

    @property
    def create_at_iso(self) -> str:
        return iso_utc(self.create_at)

    @classmethod
    def from_api(
        cls,
        raw: dict[str, Any],
        *,
        base_url: str,
        author: str = "",
        current_username: str = "",
    ) -> Post:
        post_id = str(raw.get("id", ""))
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        message = str(raw.get("message", ""))
        files = raw.get("file_ids") if isinstance(raw.get("file_ids"), list) else []
        return cls(
            id=post_id,
            channel_id=str(raw.get("channel_id", "")),
            user_id=str(raw.get("user_id", "")),
            message=message,
            create_at=int(raw.get("create_at", 0) or 0),
            update_at=int(raw.get("update_at", 0) or 0),
            delete_at=int(raw.get("delete_at", 0) or 0),
            root_id=str(raw.get("root_id", "") or ""),
            author=author,
            reply_count=int(metadata.get("reply_count", raw.get("reply_count", 0)) or 0),
            is_pinned=bool(raw.get("is_pinned", False)),
            is_mention=_contains_mention(message, current_username),
            file_ids=tuple(str(item) for item in files),
            permalink=f"{base_url.rstrip('/')}/_redirect/pl/{post_id}" if post_id else "",
        )


@dataclass(frozen=True, slots=True)
class Thread:
    id: str
    channel_id: str
    channel_name: str
    root_message: str
    reply_count: int
    last_reply_at: int
    unread_replies: int = 0
    unread_mentions: int = 0
    participant_ids: tuple[str, ...] = ()
    posts: tuple[Post, ...] = ()
    permalink: str = ""


@dataclass(frozen=True, slots=True)
class OutputEnvelope:
    profile: str
    server: str
    data: Any
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"


def primitive(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: primitive(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): primitive(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [primitive(item) for item in value]
    return value
