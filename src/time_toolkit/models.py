from __future__ import annotations

import copy
import hashlib
import json
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


def _truthy(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().casefold() in {"1", "true"}


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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
    post_type: str = ""
    edit_at: int = 0
    is_from_bot: bool = False

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
        props = raw.get("props") if isinstance(raw.get("props"), dict) else {}
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
            post_type=str(raw.get("type", "")),
            edit_at=_integer(raw.get("edit_at")),
            is_from_bot=_truthy(props.get("from_bot")),
        )


def _post_data(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("post")
    return value if isinstance(value, dict) else {}


def _reaction_data(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("reaction")
    return value if isinstance(value, dict) else {}


def _event_post_id(data: dict[str, Any]) -> str:
    post = _post_data(data)
    reaction = _reaction_data(data)
    return str(post.get("id") or data.get("post_id") or reaction.get("post_id") or "")


def _event_channel_id(data: dict[str, Any], broadcast: dict[str, Any]) -> str:
    post = _post_data(data)
    reaction = _reaction_data(data)
    return str(
        broadcast.get("channel_id")
        or data.get("channel_id")
        or post.get("channel_id")
        or reaction.get("channel_id")
        or ""
    )


def _fallback_identity(
    event: str, data: dict[str, Any], broadcast: dict[str, Any]
) -> dict[str, Any]:
    stable_data = {key: value for key, value in data.items() if key != "connection_id"}
    routing = {key: str(broadcast.get(key) or "") for key in ("channel_id", "team_id", "user_id")}
    return {"event": event, "data": stable_data, "routing": routing}


def _semantic_identity(
    event: str, data: dict[str, Any], broadcast: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    post = _post_data(data)
    reaction = _reaction_data(data)
    post_id = _event_post_id(data)
    if event == "posted" and post_id:
        return (
            "rt1",
            {
                "event": event,
                "post_id": post_id,
                "create_at": _integer(post.get("create_at")),
                "update_at": _integer(post.get("update_at")),
                "edit_at": _integer(post.get("edit_at")),
            },
        )
    if event == "post_edited" and post_id:
        return (
            "rt1",
            {
                "event": event,
                "post_id": post_id,
                "update_at": _integer(post.get("update_at")),
                "edit_at": _integer(post.get("edit_at")),
            },
        )
    if event == "post_deleted" and post_id:
        return (
            "rt1",
            {
                "event": event,
                "post_id": post_id,
                "delete_at": _integer(post.get("delete_at") or data.get("delete_at")),
            },
        )
    reaction_user_id = str(reaction.get("user_id", ""))
    reaction_emoji = str(reaction.get("emoji_name", ""))
    if (
        event in {"reaction_added", "reaction_removed"}
        and post_id
        and reaction_user_id
        and reaction_emoji
    ):
        identity = {
            "event": event,
            "post_id": post_id,
            "user_id": reaction_user_id,
            "emoji_name": reaction_emoji,
        }
        if reaction.get("create_at") is not None:
            identity["create_at"] = _integer(reaction.get("create_at"))
        return "rt1", identity
    if event == "hello":
        return (
            "rt1",
            {
                "event": event,
                "server_type": str(data.get("server_type", "")),
                "server_version": str(data.get("server_version", "")),
            },
        )
    return "rt2", _fallback_identity(event, data, broadcast)


def _semantic_key(event: str, data: dict[str, Any], broadcast: dict[str, Any]) -> str:
    version, identity = _semantic_identity(event, data, broadcast)
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"{version}:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class RealtimeConnection:
    state: str
    reconnected: bool
    connection_id: str = ""


@dataclass(frozen=True, slots=True)
class RealtimeEvent:
    event: str
    data: dict[str, Any]
    broadcast: dict[str, Any]
    seq: int
    channel_id: str = ""
    post_id: str = ""
    post: Post | None = None
    semantic_key: str = ""

    @classmethod
    def from_api(cls, raw: dict[str, Any], *, base_url: str) -> RealtimeEvent:
        event = str(raw.get("event", ""))
        raw_data = raw.get("data")
        raw_broadcast = raw.get("broadcast")
        data = copy.deepcopy(raw_data) if isinstance(raw_data, dict) else {}
        broadcast = copy.deepcopy(raw_broadcast) if isinstance(raw_broadcast, dict) else {}
        post_raw = _post_data(data)
        return cls(
            event=event,
            data=data,
            broadcast=broadcast,
            seq=_integer(raw.get("seq")),
            channel_id=_event_channel_id(data, broadcast),
            post_id=_event_post_id(data),
            post=(Post.from_api(post_raw, base_url=base_url) if post_raw else None),
            semantic_key=_semantic_key(event, data, broadcast),
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
