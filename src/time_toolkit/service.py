from __future__ import annotations

import os
import re
import tempfile
import uuid
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from time_toolkit.auth import SecretStore
from time_toolkit.client import TimeClient
from time_toolkit.config import ConfigStore, Profile
from time_toolkit.dates import iso_utc
from time_toolkit.errors import ConflictError, NotFoundError, PermissionError, UsageError
from time_toolkit.models import Channel, Post, SidebarCategory, Team, Thread, User

_ID_RE = re.compile(r"^[a-z0-9]{26}$")
_POST_URL_RE = re.compile(r"/(?:thread|pl|posts)/([a-z0-9]{20,})")
WriteMode = Literal["automated", "confirmed"]


def extract_post_id(value: str) -> str:
    candidate = value.strip()
    if _ID_RE.fullmatch(candidate):
        return candidate
    match = _POST_URL_RE.search(candidate)
    if not match:
        raise UsageError("Expected a Time post ID or a post/thread URL")
    return match.group(1)


class TimeService:
    """User-level operations shared by CLI, MCP, and future service adapters."""

    def __init__(
        self,
        profile: Profile,
        client: TimeClient,
        *,
        write_mode: WriteMode = "automated",
    ) -> None:
        if write_mode not in {"automated", "confirmed"}:
            raise ValueError(f"Unsupported write mode: {write_mode}")
        self.profile = profile
        self.client = client
        self.write_mode = write_mode
        self._me: User | None = None
        self._team_id: str = profile.team_id
        self._users: dict[str, User] = {}
        self._channels: dict[str, Channel] = {}

    @classmethod
    def open(
        cls,
        profile_name: str | None,
        *,
        config: ConfigStore | None = None,
        secrets: SecretStore | None = None,
        write_mode: WriteMode = "automated",
    ) -> TimeService:
        config_store = config or ConfigStore()
        profile = config_store.get_profile(profile_name)
        auth = (secrets or SecretStore()).context(profile.name, profile.auth_method)
        return cls(profile, TimeClient(profile.base_url, auth), write_mode=write_mode)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> TimeService:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def me(self) -> User:
        if self._me is None:
            self._me = User.from_api(self.client.get_me())
            self._users[self._me.id] = self._me
        return self._me

    def teams(self) -> list[Team]:
        return [Team.from_api(raw) for raw in self.client.get_my_teams()]

    def team_id(self) -> str:
        if self._team_id:
            return self._team_id
        teams = self.teams()
        if not teams:
            raise NotFoundError("Current account has no visible Time teams")
        self._team_id = teams[0].id
        return self._team_id

    def doctor(self) -> dict[str, Any]:
        teams = self.teams()
        return {
            "ok": True,
            "profile": self.profile.name,
            "server": self.profile.base_url,
            "ping": self.client.ping(),
            "user": self.me(),
            "teams": teams,
            "selected_team_id": self.profile.team_id or (teams[0].id if teams else ""),
            "auth_method": self.profile.auth_method,
            "mcp_enabled": self.profile.mcp_enabled,
            "write_policy": self.profile.write_policy,
        }

    # Users and channels

    def _load_users(self, user_ids: Iterable[str]) -> None:
        missing = list(dict.fromkeys(user_id for user_id in user_ids if user_id not in self._users))
        if not missing:
            return
        for raw in self.client.get_users_by_ids(missing):
            user = User.from_api(raw)
            self._users[user.id] = user

    def _post_models(self, raw_posts: Iterable[dict[str, Any]]) -> list[Post]:
        rows = list(raw_posts)
        self._load_users(str(raw.get("user_id", "")) for raw in rows)
        username = self.me().username
        return [
            Post.from_api(
                raw,
                base_url=self.profile.base_url,
                author=self._users.get(str(raw.get("user_id", "")), User("", "")).username,
                current_username=username,
            )
            for raw in rows
        ]

    def search_users(self, term: str, *, limit: int = 20) -> list[User]:
        users = [User.from_api(raw) for raw in self.client.search_users(term, limit=limit)]
        self._users.update({user.id: user for user in users})
        return users

    def get_user(self, username: str) -> User:
        user = User.from_api(self.client.get_user_by_username(username.lstrip("@")))
        self._users[user.id] = user
        return user

    def list_channels(
        self,
        *,
        pattern: str = "",
        channel_type: str = "",
        limit: int = 100,
        max_pages: int = 10,
    ) -> list[Channel]:
        needle = pattern.casefold()
        raw_matches: list[dict[str, Any]] = []
        for batch in self.client.iter_my_channels(
            self.me().id, self.team_id(), max_pages=max_pages
        ):
            for raw in batch:
                if channel_type and raw.get("type") != channel_type:
                    continue
                names = f"{raw.get('name', '')} {raw.get('display_name', '')}".casefold()
                if needle and needle not in names:
                    continue
                raw_matches.append(raw)
                if len(raw_matches) >= limit:
                    break
            if len(raw_matches) >= limit:
                break

        dm_ids: list[str] = []
        for raw in raw_matches:
            if raw.get("type") == "D" and "__" in str(raw.get("name", "")):
                dm_ids.extend(str(raw["name"]).split("__"))
        self._load_users(dm_ids)

        output: list[Channel] = []
        for raw in raw_matches:
            label = ""
            if raw.get("type") == "D" and "__" in str(raw.get("name", "")):
                ids = [item for item in str(raw["name"]).split("__") if item != self.me().id]
                names = [self._users[item].username for item in ids if item in self._users]
                label = "dm:" + ",".join(names)
            channel = Channel.from_api(raw, label=label)
            self._channels[channel.id] = channel
            output.append(channel)
        return output

    def list_dms(self, *, with_user: str = "", limit: int = 100) -> list[Channel]:
        channels = self.list_channels(limit=max(limit * 4, 200), max_pages=20)
        output = [channel for channel in channels if channel.type in {"D", "G"}]
        if with_user:
            needle = with_user.lstrip("@").casefold()
            output = [
                channel
                for channel in output
                if needle in f"{channel.label} {channel.display_name}".casefold()
            ]
        return output[:limit]

    def sidebar_categories(self) -> list[SidebarCategory]:
        payload = self.client.get_sidebar_categories(self.me().id, self.team_id())
        raw_categories = payload.get("categories")
        raw_order = payload.get("order")
        categories = (
            [SidebarCategory.from_api(raw) for raw in raw_categories if isinstance(raw, dict)]
            if isinstance(raw_categories, list)
            else []
        )
        by_id = {category.id: category for category in categories}
        ordered: list[SidebarCategory] = []
        seen: set[str] = set()
        if isinstance(raw_order, list):
            for category_id in raw_order:
                key = str(category_id)
                category = by_id.get(key)
                if category is not None and key not in seen:
                    ordered.append(category)
                    seen.add(key)
        for category in categories:
            if category.id not in seen:
                ordered.append(category)
                seen.add(category.id)
        return ordered

    def resolve_sidebar_category(self, value: str) -> SidebarCategory:
        target = value.strip()
        categories = self.sidebar_categories()
        for category in categories:
            if category.id == target:
                return category
        matches = [category for category in categories if category.display_name == target]
        if not matches:
            raise NotFoundError(f"Sidebar category not found: {value}")
        if len(matches) > 1:
            raise ConflictError(
                f"Sidebar category name is ambiguous: {value}",
                details={
                    "matches": [{"id": category.id, "type": category.type} for category in matches]
                },
            )
        return matches[0]

    def category_channels(self, value: str) -> list[Channel]:
        category = self.resolve_sidebar_category(value)
        if not category.channel_ids:
            return []
        visible = {
            channel.id: channel
            for channel in self.list_channels(
                limit=max(100, len(category.channel_ids)),
                max_pages=20,
            )
        }
        output: list[Channel] = []
        for channel_id in category.channel_ids:
            channel = visible.get(channel_id)
            if channel is None:
                try:
                    channel = Channel.from_api(self.client.get_channel(channel_id))
                except (NotFoundError, PermissionError):
                    continue
                if not channel.id:
                    continue
                self._channels[channel.id] = channel
                visible[channel_id] = channel
            output.append(channel)
        return output

    def resolve_channel(self, value: str) -> Channel:
        target = value.strip().lstrip("~")
        if _ID_RE.fullmatch(target):
            if target not in self._channels:
                self._channels[target] = Channel.from_api(self.client.get_channel(target))
            return self._channels[target]
        try:
            raw = self.client.get_channel_by_name(self.team_id(), target)
        except NotFoundError:
            raw = None
        if raw:
            channel = Channel.from_api(raw)
            self._channels[channel.id] = channel
            return channel
        matches = self.list_channels(pattern=target, limit=20, max_pages=20)
        if not matches:
            raise NotFoundError(f"Channel not found: {value}")
        if len(matches) > 1:
            raise ConflictError(
                f"Channel name is ambiguous: {value}",
                details={"matches": [channel.label for channel in matches]},
            )
        return matches[0]

    # Reading

    def channel_posts(
        self,
        channel: str,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        authors: list[str] | None = None,
        contains: str = "",
        limit: int = 100,
    ) -> list[Post]:
        channel_id = self.resolve_channel(channel).id
        raw_posts: list[dict[str, Any]] = []
        if since_ms is not None:
            raw_posts = self.client.get_channel_posts_page(channel_id, since_ms=since_ms)
        else:
            page = 0
            while len(raw_posts) < max(limit * 2, 100):
                batch = self.client.get_channel_posts_page(channel_id, page=page, per_page=100)
                if not batch:
                    break
                raw_posts.extend(batch)
                if len(batch) < 100:
                    break
                page += 1

        posts = self._post_models(raw_posts)
        author_set = {author.lstrip("@").casefold() for author in authors or []}
        needle = contains.casefold()
        output = [
            post
            for post in posts
            if (since_ms is None or post.create_at >= since_ms)
            and (until_ms is None or post.create_at <= until_ms)
            and (not author_set or post.author.casefold() in author_set)
            and (not needle or needle in post.message.casefold())
        ]
        output.sort(key=lambda post: post.create_at, reverse=True)
        return output[:limit]

    def search_posts(
        self,
        query: str,
        *,
        channels: list[str] | None = None,
        authors: list[str] | None = None,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int = 100,
    ) -> list[Post]:
        channel_ids = {self.resolve_channel(channel).id for channel in channels or []}
        author_names = [author.lstrip("@") for author in authors or []]
        queries = [query.strip()]
        if author_names:
            queries = [f"{query.strip()} from:{author}".strip() for author in author_names]
        if since_ms:
            queries = [f"{terms} after:{iso_utc(since_ms)[:10]}".strip() for terms in queries]
        if until_ms:
            queries = [f"{terms} before:{iso_utc(until_ms)[:10]}".strip() for terms in queries]

        raw_by_id: dict[str, dict[str, Any]] = {}
        for terms in queries:
            query_matches = 0
            page_size = min(max(limit, 60), 200)
            for page in range(100):
                batch = self.client.search_posts(
                    self.team_id(), terms, page=page, per_page=page_size
                )
                if not batch:
                    break
                before = len(raw_by_id)
                raw_by_id.update(
                    {str(post.get("id", "")): post for post in batch if post.get("id")}
                )
                added = len(raw_by_id) - before
                query_matches += added
                if len(batch) < page_size or added == 0 or query_matches >= max(limit * 2, 100):
                    break

        posts = self._post_models(raw_by_id.values())
        author_set = {author.casefold() for author in author_names}
        output = [
            post
            for post in posts
            if (not channel_ids or post.channel_id in channel_ids)
            and (not author_set or post.author.casefold() in author_set)
            and (since_ms is None or post.create_at >= since_ms)
            and (until_ms is None or post.create_at <= until_ms)
        ]
        output.sort(key=lambda post: post.create_at, reverse=True)
        return output[:limit]

    def thread(self, value: str) -> Thread:
        root_id = extract_post_id(value)
        raw_posts = self.client.get_thread(root_id)
        if not raw_posts:
            raise NotFoundError(f"Thread not found: {value}")
        posts = self._post_models(raw_posts)
        posts.sort(key=lambda post: post.create_at)
        root = next((post for post in posts if post.id == root_id), posts[0])
        channel = self.resolve_channel(root.channel_id)
        return Thread(
            id=root_id,
            channel_id=root.channel_id,
            channel_name=channel.label,
            root_message=root.message,
            reply_count=max(len(posts) - 1, root.reply_count),
            last_reply_at=posts[-1].create_at,
            participant_ids=tuple(sorted({post.user_id for post in posts if post.user_id})),
            posts=tuple(posts),
            permalink=f"{self.profile.base_url}/_redirect/pl/{root_id}",
        )

    def followed_threads(self, *, limit: int = 100, with_posts: bool = False) -> list[Thread]:
        raw_threads = self.client.get_followed_threads(self.me().id, self.team_id())
        output: list[Thread] = []
        for raw in raw_threads[:limit]:
            root = raw.get("post") if isinstance(raw.get("post"), dict) else {}
            root_id = str(raw.get("id") or root.get("id") or "")
            channel_id = str(root.get("channel_id", ""))
            channel_name = self.resolve_channel(channel_id).label if channel_id else ""
            posts: tuple[Post, ...] = ()
            if with_posts and root_id:
                posts = tuple(self._post_models(self.client.get_thread(root_id)))
            participants = (
                raw.get("participants") if isinstance(raw.get("participants"), list) else []
            )
            participant_ids = tuple(
                str(item.get("id", "")) if isinstance(item, dict) else str(item)
                for item in participants
            )
            output.append(
                Thread(
                    id=root_id,
                    channel_id=channel_id,
                    channel_name=channel_name,
                    root_message=str(root.get("message", "")),
                    reply_count=int(raw.get("reply_count", 0) or 0),
                    last_reply_at=int(raw.get("last_reply_at", 0) or 0),
                    unread_replies=int(raw.get("unread_replies", 0) or 0),
                    unread_mentions=int(raw.get("unread_mentions", 0) or 0),
                    participant_ids=participant_ids,
                    posts=posts,
                    permalink=f"{self.profile.base_url}/_redirect/pl/{root_id}",
                )
            )
        return output

    def unread(
        self,
        *,
        channel: str = "",
        with_posts: bool = False,
        mentions_only: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        me = self.me()
        if channel:
            resolved = self.resolve_channel(channel)
            result: dict[str, Any] = {
                "team": self.client.get_team_unread(me.id, self.team_id()),
                "channel": resolved,
                "unread": self.client.get_channel_unread(me.id, resolved.id),
            }
            if with_posts:
                posts = self._post_models(
                    self.client.get_posts_around_unread(
                        me.id, resolved.id, limit_before=0, limit_after=limit
                    )
                )
                result["posts"] = [post for post in posts if not mentions_only or post.is_mention]
            return result

        channels = self.list_channels(limit=10_000, max_pages=0)
        channels_by_id = {item.id: item for item in channels}
        memberships = self.client.get_channel_members_for_user(me.id, self.team_id())
        rows: list[dict[str, Any]] = []
        for membership in memberships:
            channel_id = str(membership.get("channel_id", ""))
            item = channels_by_id.get(channel_id)
            if item is None:
                continue
            current_count = item.total_msg_count
            viewed_count = int(membership.get("msg_count", 0) or 0)
            unread_count = max(0, current_count - viewed_count)
            mention_count = int(membership.get("mention_count", 0) or 0)
            if unread_count == 0 and mention_count == 0:
                continue
            if mentions_only and mention_count == 0:
                continue
            row: dict[str, Any] = {
                "channel": item,
                "unread_count": unread_count,
                "mention_count": mention_count,
                "last_viewed_at": int(membership.get("last_viewed_at", 0) or 0),
            }
            if with_posts:
                posts = self._post_models(
                    self.client.get_posts_around_unread(
                        me.id, channel_id, limit_before=0, limit_after=limit
                    )
                )
                row["posts"] = [post for post in posts if not mentions_only or post.is_mention]
            rows.append(row)
        rows.sort(
            key=lambda row: (int(row["mention_count"]), int(row["unread_count"])),
            reverse=True,
        )
        return {
            "team": self.client.get_team_unread(me.id, self.team_id()),
            "threads": self.client.get_thread_stats(me.id, self.team_id()),
            "channels": rows,
        }

    def flagged_posts(self, *, channel: str = "", limit: int = 100) -> list[Post]:
        channel_id = self.resolve_channel(channel).id if channel else ""
        raw: list[dict[str, Any]] = []
        page = 0
        while len(raw) < limit:
            batch = self.client.get_flagged_posts(
                self.me().id,
                team_id=self.team_id(),
                channel_id=channel_id,
                page=page,
                per_page=min(limit, 100),
            )
            if not batch:
                break
            raw.extend(batch)
            if len(batch) < min(limit, 100):
                break
            page += 1
        posts = self._post_models(raw)
        posts.sort(key=lambda post: post.create_at, reverse=True)
        return posts[:limit]

    def pinned_posts(self, channel: str) -> list[Post]:
        channel_id = self.resolve_channel(channel).id
        posts = self._post_models(self.client.get_pinned_posts(channel_id))
        posts.sort(key=lambda post: post.create_at, reverse=True)
        return posts

    def user_activity(
        self,
        username: str,
        *,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        user = self.get_user(username)
        posts = self.search_posts(
            "",
            authors=[user.username],
            since_ms=since_ms,
            until_ms=until_ms,
            limit=limit,
        )
        channel_ids = list(dict.fromkeys(post.channel_id for post in posts if post.channel_id))
        channels: dict[str, Channel] = {}
        for raw in self.client.get_channels_by_ids(channel_ids):
            item = Channel.from_api(raw)
            channels[item.id] = item
        counts = Counter(
            channels.get(post.channel_id, Channel("", "", "", "")).label for post in posts
        )
        counts.pop("", None)
        thread_ids = {post.root_id or post.id for post in posts}
        return {
            "user": user,
            "post_count": len(posts),
            "thread_count": len(thread_ids),
            "by_channel": [
                {"channel": channel, "posts": count} for channel, count in counts.most_common()
            ],
            "posts": posts,
        }

    # Writes

    def _require_write_allowed(self) -> None:
        if self.profile.write_policy == "readonly":
            raise PermissionError(
                f"Writes are disabled for the {self.profile.name} profile; "
                f"change the policy with: timetk profile update {self.profile.name} "
                "--write-policy approval"
            )
        if self.write_mode == "confirmed":
            return
        if self.profile.write_policy != "fullauto":
            raise PermissionError(
                f"Automated writes require the fullauto policy for the {self.profile.name} "
                f"profile; change it with: timetk profile update {self.profile.name} "
                "--write-policy fullauto"
            )

    def create_post(
        self,
        target: str,
        message: str,
        *,
        file_ids: list[str] | None = None,
        idempotency_key: str = "",
    ) -> Post:
        self._require_write_allowed()
        if not message.strip() and not file_ids:
            raise UsageError("Message and files cannot both be empty")
        key = idempotency_key or uuid.uuid4().hex
        if target.startswith("@"):
            raw = self.client.create_post(
                message, peer=target, file_ids=file_ids, idempotency_key=key
            )
        else:
            channel = self.resolve_channel(target)
            raw = self.client.create_post(
                message, channel_id=channel.id, file_ids=file_ids, idempotency_key=key
            )
        return self._post_models([raw])[0]

    def reply(
        self,
        target: str,
        message: str,
        *,
        file_ids: list[str] | None = None,
        idempotency_key: str = "",
    ) -> Post:
        self._require_write_allowed()
        if not message.strip() and not file_ids:
            raise UsageError("Message and files cannot both be empty")
        root_id = extract_post_id(target)
        root = self.client.get_post(root_id)
        key = idempotency_key or uuid.uuid4().hex
        raw = self.client.create_post(
            message,
            channel_id=str(root.get("channel_id", "")),
            root_id=root_id,
            file_ids=file_ids,
            idempotency_key=key,
        )
        return self._post_models([raw])[0]

    def edit_post(self, target: str, message: str) -> Post:
        self._require_write_allowed()
        if not message.strip():
            raise UsageError("Edited message cannot be empty")
        post_id = extract_post_id(target)
        raw = self.client.update_post(post_id, message, idempotency_key=uuid.uuid4().hex)
        return self._post_models([raw])[0]

    def delete_post(self, target: str) -> dict[str, Any]:
        self._require_write_allowed()
        post_id = extract_post_id(target)
        self.client.delete_post(post_id, idempotency_key=uuid.uuid4().hex)
        return {"deleted": True, "post_id": post_id}

    def pin_post(self, target: str, *, pinned: bool) -> dict[str, Any]:
        self._require_write_allowed()
        post_id = extract_post_id(target)
        self.client.pin_post(post_id, pinned=pinned, idempotency_key=uuid.uuid4().hex)
        return {"post_id": post_id, "pinned": pinned}

    def react(self, target: str, emoji: str, *, add: bool) -> dict[str, Any]:
        self._require_write_allowed()
        post_id = extract_post_id(target)
        key = uuid.uuid4().hex
        if add:
            self.client.add_reaction(self.me().id, post_id, emoji, idempotency_key=key)
        else:
            self.client.remove_reaction(self.me().id, post_id, emoji, idempotency_key=key)
        return {"post_id": post_id, "emoji": emoji, "added": add}

    def flag_post(self, target: str, *, flagged: bool) -> dict[str, Any]:
        self._require_write_allowed()
        post_id = extract_post_id(target)
        self.client.set_flagged(
            self.me().id,
            post_id,
            flagged=flagged,
            idempotency_key=uuid.uuid4().hex,
        )
        return {"post_id": post_id, "flagged": flagged}

    def follow_thread(self, target: str, *, following: bool) -> dict[str, Any]:
        self._require_write_allowed()
        thread_id = extract_post_id(target)
        self.client.follow_thread(
            self.me().id,
            self.team_id(),
            thread_id,
            following=following,
            idempotency_key=uuid.uuid4().hex,
        )
        return {"thread_id": thread_id, "following": following}

    def mark_unread(self, target: str) -> dict[str, Any]:
        self._require_write_allowed()
        post_id = extract_post_id(target)
        result = self.client.set_post_unread(
            self.me().id, post_id, idempotency_key=uuid.uuid4().hex
        )
        return {"post_id": post_id, "unread": True, "result": result}

    def mark_channel_read(self, channel: str) -> dict[str, Any]:
        self._require_write_allowed()
        resolved = self.resolve_channel(channel)
        result = self.client.view_channel(self.me().id, resolved.id)
        return {"channel": resolved, "read": True, "result": result}

    def reactions(self, target: str) -> list[dict[str, Any]]:
        return self.client.get_post_reactions(extract_post_id(target))

    def readers(self, targets: list[str]) -> dict[str, Any]:
        return self.client.get_post_readers([extract_post_id(target) for target in targets])

    def file_info(self, file_id: str) -> dict[str, Any]:
        return self.client.get_file_info(file_id)

    def download_file(self, file_id: str, destination: Path, *, overwrite: bool = False) -> Path:
        if destination.exists() and not overwrite:
            raise ConflictError(f"Destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = ""
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                delete=False,
            ) as handle:
                temporary = handle.name
            self.client.download_file_to(file_id, Path(temporary))
            os.replace(temporary, destination)
        except OSError as exc:
            raise UsageError(f"Cannot write file: {destination}") from exc
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return destination

    def upload_files(self, channel: str, files: list[Path]) -> list[str]:
        self._require_write_allowed()
        channel_id = self.resolve_channel(channel).id
        file_ids: list[str] = []
        for path in files:
            value = self.client.upload_file(channel_id, path)
            infos = value.get("file_infos") if isinstance(value.get("file_infos"), list) else []
            file_ids.extend(str(info.get("id", "")) for info in infos if info.get("id"))
        return file_ids
