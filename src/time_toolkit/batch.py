"""Explicitly approved, resumable local batches; no message bodies are persisted."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import httpx

from time_toolkit.client import TimeClient
from time_toolkit.errors import (
    AuthenticationError,
    ConflictError,
    NetworkError,
    TimeToolkitError,
    UsageError,
)
from time_toolkit.service import TimeService


class RateGate:
    def __init__(self, requests_per_second: float):
        if not 0 < requests_per_second <= 20:
            raise UsageError("requests_per_second must be between 0 and 20")
        self.interval = 1 / requests_per_second
        self.next_request = 0.0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic()
            scheduled = max(now, self.next_request)
            self.next_request = scheduled + self.interval
        time.sleep(max(0.0, scheduled - now))


class LimitedClient(TimeClient):
    def __init__(self, source: TimeClient, gate: RateGate):
        super().__init__(source.base_url, source.auth)
        self._http.close()
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=4, max_keepalive_connections=4, keepalive_expiry=60
            ),
        )
        self.gate = gate

    def request(self, *args, **kwargs):
        self.gate.wait()
        return super().request(*args, **kwargs)


class BatchState:
    def __init__(self, path: Path, *, create: bool = False):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if create:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
        elif not self.path.is_file():
            raise UsageError("Batch state does not exist")
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        if create:
            self.db.executescript("""
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE channels (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, team_id TEXT NOT NULL,
                    type TEXT NOT NULL, was_member INTEGER NOT NULL,
                    needs_join INTEGER NOT NULL, joined INTEGER NOT NULL DEFAULT 0,
                    moved INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE posts (
                    id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, has_reactions INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX posts_status ON posts(status);
                CREATE TABLE post_selection (
                    post_id TEXT PRIMARY KEY, kind TEXT NOT NULL, root_id TEXT NOT NULL
                );
                CREATE TABLE thread_roots (
                    id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
                    reply_count INTEGER, replies_fetched INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE prior_receipts (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, attempts INTEGER NOT NULL
                );
            """)
            self.put("schema_version", 1)
        if self.get("schema_version") != 1:
            raise UsageError("Unsupported batch state")

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def summary(self):
        channels = self.db.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        new = self.db.execute("SELECT COUNT(*) FROM channels WHERE needs_join=1").fetchone()[0]
        states = dict(
            self.db.execute("SELECT status,COUNT(*) FROM posts GROUP BY status").fetchall()
        )
        unfinished_channels = self.db.execute(
            "SELECT COUNT(*) FROM channels WHERE fetched=0 OR (needs_join=1 AND moved=0)"
        ).fetchone()[0]
        channel_errors = self.db.execute(
            "SELECT COUNT(*) FROM channels WHERE error!=''"
        ).fetchone()[0]
        complete = (
            not unfinished_channels
            and not channel_errors
            and all(status in {"added", "already_present"} for status in states)
        )
        return {
            "run_id": self.get("run_id"),
            "profile": self.get("profile"),
            "server": self.get("server"),
            "channels": channels,
            "new_channels": new,
            "folders": self.get("folders", {}),
            "emoji": self.get("emoji"),
            "limit": self.get("limit"),
            "thread_reply_limit": self.get("thread_reply_limit", 0),
            "selection_mode": self.get("selection_mode", "channel_latest"),
            "channel_types": self.get("channel_types", ["O", "P", "D", "G"]),
            "estimated_posts": self.get("estimated_posts"),
            "posts": states,
            "channel_errors": channel_errors,
            "unfinished_channels": unfinished_channels,
            "complete": complete,
            "approval_digest": self.get("approval_digest"),
        }

    def close(self):
        self.db.close()


def create_plan(
    service: TimeService,
    state: BatchState,
    *,
    emoji: str,
    folder: str,
    limit: int,
    channel_types: list[str] | None = None,
    channel_ids: list[str] | None = None,
):
    requested_ids = set(channel_ids) if channel_ids is not None else None
    if requested_ids is not None and (
        not requested_ids
        or any(not re.fullmatch(r"[a-z0-9]{26}", value) for value in requested_ids)
    ):
        raise UsageError("Choose at least one exact channel ID")
    selected_types = sorted(
        set(channel_types if channel_types is not None else ["O", "P", "D", "G"])
    )
    if not selected_types or not set(selected_types) <= {"O", "P", "D", "G"}:
        raise UsageError("Choose at least one channel type from O, P, D, G")
    if not 1 <= limit <= 200 or not folder.strip() or len(folder.strip()) > 54:
        raise UsageError(
            "Choose a non-empty folder name up to 54 characters and limit from 1 to 200"
        )
    emoji = emoji.strip(": ")
    if not emoji or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in emoji):
        raise UsageError("Use an exact custom emoji name")
    found = service.client.request("GET", f"/api/v4/emoji/name/{emoji}")
    if not isinstance(found, dict) or found.get("name") != emoji or found.get("delete_at"):
        raise UsageError("The requested active custom emoji was not found")
    user_id = service.me().id
    teams = service.client.get_my_teams()
    raw_channels, member_ids = {}, set()
    folders = {}
    backups = {}
    for team in teams:
        team_id = team["id"]
        rows = service.client.request("GET", f"/api/v4/users/{user_id}/teams/{team_id}/channels")
        if not isinstance(rows, list):
            raise UsageError("Invalid channel membership list")
        for row in rows:
            member_ids.add(row["id"])
            raw_channels[row["id"]] = {**row, "collection_team": team_id}
        seen = set()
        for page in range(1000):
            rows = service.client.get_public_channels_page(team_id, page=page, per_page=100)
            signature = tuple(row["id"] for row in rows)
            if rows and signature in seen:
                raise UsageError("The public catalog repeats pages")
            seen.add(signature)
            for row in rows:
                if row.get("type") == "O" and not row.get("delete_at"):
                    raw_channels[row["id"]] = {**row, "collection_team": team_id}
            if len(rows) < 100:
                break
        else:
            raise UsageError("Public catalog exceeded the page bound")
        payload = service.client.get_sidebar_categories(user_id, team_id)
        backups[team_id] = payload
        names = {row.get("display_name") for row in payload.get("categories", [])}
        name = folder.strip()
        index = 2
        while name in names:
            name = f"{folder.strip()} ({index})"
            index += 1
        folders[team_id] = {"name": name, "id": ""}
    estimated_posts = 0
    planned_ids = set()
    for identifier, row in raw_channels.items():
        if requested_ids is not None and identifier not in requested_ids:
            continue
        count = row.get("total_msg_count")
        if not isinstance(count, int) or count <= 0 or row.get("type") not in selected_types:
            continue
        was_member = identifier in member_ids
        if not was_member and (row.get("type") != "O" or row.get("delete_at")):
            continue
        if not re.fullmatch(r"[a-z0-9]{26}", identifier):
            raise UsageError("Invalid channel ID in the Time directory")
        estimated_posts += min(count, limit)
        planned_ids.add(identifier)
        state.db.execute(
            "INSERT INTO channels (id,name,team_id,type,was_member,needs_join,joined) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                identifier,
                row.get("display_name") or row.get("name") or identifier,
                row.get("team_id") or row["collection_team"],
                row["type"],
                was_member,
                not was_member,
                was_member,
            ),
        )
    if requested_ids is not None and requested_ids != planned_ids:
        raise UsageError(
            "Some selected channels are missing or do not match the non-empty type scope"
        )
    state.db.commit()
    for key, value in {
        "run_id": uuid.uuid4().hex,
        "profile": service.profile.name,
        "server": service.profile.base_url,
        "user_id": user_id,
        "emoji": emoji,
        "limit": limit,
        "channel_types": selected_types,
        "folders": folders,
        "sidebar_before": backups,
        "estimated_posts": estimated_posts,
    }.items():
        state.put(key, value)
    manifest = {
        "run_id": state.get("run_id"),
        "profile": service.profile.name,
        "server": service.profile.base_url,
        "user_id": user_id,
        "emoji": emoji,
        "limit": limit,
        "channel_types": selected_types,
        "folders": {key: value["name"] for key, value in folders.items()},
        "channels": [
            tuple(row)
            for row in state.db.execute(
                "SELECT id,team_id,type,was_member,needs_join FROM channels ORDER BY id"
            )
        ],
    }
    state.put("approval_manifest", manifest)
    state.put(
        "approval_digest", hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    )
    lines = [
        "# План пакетной обработки Time",
        "",
        f"Профиль: {service.profile.name}. Сервер: {service.profile.base_url}.",
        "",
        f"Реакция: :{emoji}:. Последних сообщений на канал: до {limit}.",
        "",
        f"Типы каналов: {', '.join(selected_types)}.",
        "",
        f"Оценка общего числа сообщений по текущим счётчикам: около {estimated_posts}.",
        "",
        "Вступление и перенос в новую папку выполняются только для каналов вне исходного членства.",
        "Существующие каналы и беседы не перемещаются. Префиксы BSc/MSc не исключаются.",
        "",
        "Подтверждения для запуска:",
        "",
        f"- run_id: `{state.get('run_id')}`",
        f"- approval_digest: `{state.get('approval_digest')}`",
        "",
        "## Выбранные каналы и беседы",
        "",
    ]
    for row in state.db.execute("SELECT * FROM channels ORDER BY name,id"):
        name = row["name"].replace("\n", " ").replace("\r", " ").replace("|", "\\|")
        action = "вступить и перенести" if row["needs_join"] else "оставить на месте"
        lines.append(f"- {name} (`{row['id']}`, {row['type']}): {action}")
    report = state.path.with_suffix(".plan.md")
    descriptor = os.open(report, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return state.summary()


def move_payload(
    payload: dict[str, Any], target_id: str, identifiers: list[str], user_id: str, team_id: str
):
    rows = copy.deepcopy(payload.get("categories"))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise UsageError("Invalid sidebar snapshot")
    target = next(
        (row for row in rows if row.get("id") == target_id and row.get("type") == "custom"), None
    )
    if target is None:
        raise UsageError("Destination folder is missing")
    moving = set(identifiers)
    previous = list(target.get("channel_ids", []))
    for row in rows:
        if row.get("user_id") != user_id or row.get("team_id") != team_id:
            raise UsageError("Sidebar owner does not match the batch account")
        values = row.get("channel_ids", [])
        if not isinstance(values, list):
            raise UsageError("Invalid folder channel list")
        row["channel_ids"] = [value for value in values if value not in moving]
    target["channel_ids"] = list(dict.fromkeys(previous + identifiers))
    return rows


def apply_membership(service: TimeService, state: BatchState):
    client, user_id = service.client, state.get("user_id")
    current = set()
    for team_id in state.get("folders"):
        for row in client.request("GET", f"/api/v4/users/{user_id}/teams/{team_id}/channels"):
            current.add(row["id"])
    pending = state.db.execute(
        "SELECT * FROM channels WHERE needs_join=1 AND (joined=0 OR moved=0) ORDER BY rowid"
    ).fetchall()
    for index, row in enumerate(pending, 1):
        try:
            if row["id"] not in current:
                raw = client.get_channel(row["id"])
                if (
                    raw.get("type") != "O"
                    or raw.get("delete_at")
                    or raw.get("team_id") != row["team_id"]
                ):
                    raise UsageError("Channel no longer matches the approved public-channel plan")
                client.join_channel(
                    user_id, row["id"], idempotency_key=f"{state.get('run_id')}-join-{row['id']}"
                )
                current.add(row["id"])
            state.db.execute("UPDATE channels SET joined=1,error='' WHERE id=?", (row["id"],))
        except (AuthenticationError, NetworkError):
            raise
        except TimeToolkitError as error:
            state.db.execute("UPDATE channels SET error=? WHERE id=?", (error.message, row["id"]))
            state.db.commit()
            continue
        state.db.commit()
        try:
            move_joined_channels(service, state)
        except TimeToolkitError as error:
            state.db.execute("UPDATE channels SET error=? WHERE id=?", (error.message, row["id"]))
            state.db.commit()
            raise
        if index % 25 == 0 or index == len(pending):
            print(
                json.dumps({"phase": "joining", "processed": index, "total": len(pending)}),
                flush=True,
            )


def move_joined_channels(service: TimeService, state: BatchState):
    """File each successful join before any subsequent membership operation."""
    client, user_id = service.client, state.get("user_id")
    folders = state.get("folders")
    for team_id, destination in folders.items():
        identifiers = [
            row[0]
            for row in state.db.execute(
                "SELECT id FROM channels WHERE team_id=? AND needs_join=1 AND joined=1 AND moved=0",
                (team_id,),
            )
        ]
        if not identifiers:
            continue
        payload = client.get_sidebar_categories(user_id, team_id)
        if not destination["id"]:
            existing = [
                row
                for row in payload.get("categories", [])
                if row.get("display_name") == destination["name"] and row.get("type") == "custom"
            ]
            if len(existing) > 1:
                raise UsageError("Destination folder became ambiguous")
            raw = (
                existing[0]
                if existing
                else client.create_sidebar_category(
                    user_id,
                    team_id,
                    destination["name"],
                    idempotency_key=f"{state.get('run_id')}-folder-{team_id}",
                )
            )
            destination["id"] = raw["id"]
            state.put("folders", folders)
            payload = client.get_sidebar_categories(user_id, team_id)
        updated = move_payload(payload, destination["id"], identifiers, user_id, team_id)
        if updated != payload["categories"]:
            revision = hashlib.sha256(json.dumps(updated, sort_keys=True).encode()).hexdigest()[:16]
            client.update_sidebar_categories(
                user_id,
                team_id,
                updated,
                idempotency_key=f"{state.get('run_id')}-move-{team_id}-{revision}",
            )
        state.db.executemany(
            "UPDATE channels SET moved=1,error='' WHERE id=?",
            [(identifier,) for identifier in identifiers],
        )
        state.db.commit()


def collect_posts(service: TimeService, state: BatchState):
    if state.get("selection_mode") == "roots_and_replies":
        from time_toolkit.thread_batch import collect_roots_and_replies

        return collect_roots_and_replies(service, state)
    limit = state.get("limit")
    pending = state.db.execute("SELECT id FROM channels WHERE joined=1 AND fetched=0").fetchall()
    for index, row in enumerate(pending, 1):
        try:
            raw_channel = service.client.get_channel(row["id"])
            allowed_types = state.get("channel_types", ["O", "P", "D", "G"])
            if raw_channel.get("type") not in allowed_types:
                raise UsageError("Channel type no longer matches the approved scope")
            posts = service.client.get_channel_posts_page(row["id"], per_page=limit)
            posts = sorted(posts, key=lambda post: post.get("create_at", 0), reverse=True)[:limit]
            for post in posts:
                if post.get("delete_at"):
                    continue
                if post.get("channel_id") != row["id"]:
                    raise UsageError("Time returned a post from a different channel")
                if not re.fullmatch(r"[a-z0-9]{26}", post.get("id", "")):
                    raise UsageError("Time returned an invalid post ID")
                has_reactions = post.get("has_reactions")
                state.db.execute(
                    "INSERT OR IGNORE INTO posts(id,channel_id,has_reactions) VALUES(?,?,?)",
                    (
                        post["id"],
                        row["id"],
                        int(has_reactions) if isinstance(has_reactions, bool) else None,
                    ),
                )
            state.db.execute("UPDATE channels SET fetched=1,error='' WHERE id=?", (row["id"],))
        except (AuthenticationError, NetworkError):
            raise
        except TimeToolkitError as error:
            state.db.execute(
                "UPDATE channels SET fetched=1,error=? WHERE id=?", (error.message, row["id"])
            )
        state.db.commit()
        if index % 50 == 0 or index == len(pending):
            print(
                json.dumps(
                    {"phase": "collecting_post_ids", "processed": index, "total": len(pending)}
                ),
                flush=True,
            )


def react_one(client: TimeClient, row: dict[str, Any], *, user_id: str, emoji: str, run_id: str):
    if row["attempts"] or row["has_reactions"] is None or row["has_reactions"]:
        existing = client.get_post_reactions(row["id"])
        if any(
            item.get("user_id") == user_id and item.get("emoji_name") == emoji for item in existing
        ):
            return "already_present"
    try:
        client.add_reaction(user_id, row["id"], emoji, idempotency_key=f"{run_id}-{row['id']}")
    except ConflictError:
        existing = client.get_post_reactions(row["id"])
        if any(
            item.get("user_id") == user_id and item.get("emoji_name") == emoji for item in existing
        ):
            return "already_present"
        raise
    return "added"


def apply_reactions(service: TimeService, state: BatchState, *, workers: int):
    if not 1 <= workers <= 8:
        raise UsageError("workers must be between 1 and 8")
    last_report = 0.0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        reserved = set()
        fatal = None

        def fill_slots():
            rows = [
                dict(row)
                for row in state.db.execute(
                    "SELECT * FROM posts WHERE status='pending' ORDER BY rowid LIMIT ?",
                    (workers * 2,),
                )
                if row["id"] not in reserved
            ][: workers - len(futures)]
            state.db.executemany(
                "UPDATE posts SET attempts=attempts+1 WHERE id=?", [(row["id"],) for row in rows]
            )
            state.db.commit()
            for row in rows:
                reserved.add(row["id"])
                future = pool.submit(
                    react_one,
                    service.client,
                    row,
                    user_id=state.get("user_id"),
                    emoji=state.get("emoji"),
                    run_id=state.get("run_id"),
                )
                futures[future] = row["id"]

        fill_slots()
        while futures:
            completed, _pending = wait(futures, return_when=FIRST_COMPLETED)
            for future in completed:
                identifier = futures.pop(future)
                reserved.remove(identifier)
                try:
                    status = future.result()
                    error = ""
                except (AuthenticationError, NetworkError) as failure:
                    status, error, fatal = "pending", failure.message, failure
                except TimeToolkitError as failure:
                    if failure.status_code in {408, 425, 429, 500, 502, 503, 504}:
                        status, error = "pending", failure.message
                        fatal = NetworkError("Temporary Time response; progress preserved")
                    else:
                        status, error = "failed", failure.message
                except Exception as failure:
                    status, error, fatal = "pending", type(failure).__name__, failure
                state.db.execute(
                    "UPDATE posts SET status=?,error=? WHERE id=?", (status, error, identifier)
                )
                state.db.commit()
            if fatal is None:
                fill_slots()
            if time.monotonic() - last_report >= 30:
                print(json.dumps(state.summary(), ensure_ascii=False), flush=True)
                last_report = time.monotonic()
        if fatal is not None:
            raise fatal


def run_batch(state: BatchState, *, approve_run: str, confirm_plan: str, workers: int, rate: float):
    if not 1 <= workers <= 8:
        raise UsageError("workers must be between 1 and 8")
    gate = RateGate(rate)
    if approve_run != state.get("run_id") or confirm_plan != state.get("approval_digest"):
        raise UsageError("Both exact run and plan confirmations are required")
    manifest = state.get("approval_manifest")
    if hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest() != confirm_plan:
        raise UsageError("Approved plan integrity check failed")
    current_rows = [
        list(row)
        for row in state.db.execute(
            "SELECT id,team_id,type,was_member,needs_join FROM channels ORDER BY id"
        )
    ]
    if current_rows != manifest["channels"]:
        raise UsageError("The channel list changed after approval")
    for key in ("run_id", "profile", "server", "user_id", "emoji", "limit"):
        if state.get(key) != manifest[key]:
            raise UsageError("Batch parameters changed after approval")
    if "channel_types" in manifest and state.get("channel_types") != manifest["channel_types"]:
        raise UsageError("Channel types changed after approval")
    for key in ("selection_mode", "thread_reply_limit"):
        if key in manifest and state.get(key) != manifest[key]:
            raise UsageError("Thread selection changed after approval")
    if {key: value["name"] for key, value in state.get("folders").items()} != manifest["folders"]:
        raise UsageError("Folder names changed after approval")
    if state.db.execute(
        "SELECT COUNT(*) FROM posts LEFT JOIN channels ON posts.channel_id=channels.id "
        "WHERE channels.id IS NULL OR channels.joined!=1"
    ).fetchone()[0]:
        raise UsageError("Post progress refers to a channel outside the approved membership")
    if state.get("selection_mode") == "roots_and_replies":
        from time_toolkit.thread_batch import validate_thread_selection

        validate_thread_selection(state)
    elif state.db.execute(
        "SELECT channel_id FROM posts GROUP BY channel_id HAVING COUNT(*)>?",
        (state.get("limit"),),
    ).fetchone():
        raise UsageError("Post progress exceeds the approved per-channel limit")
    try:
        import fcntl
    except ImportError as error:
        raise UsageError("The batch runner currently requires macOS or Linux") from error
    lock_path = state.path.with_suffix(state.path.suffix + ".lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise UsageError("This batch is already running") from error
        with TimeService.open(state.get("profile"), write_mode="confirmed") as service:
            state.db.execute("CREATE INDEX IF NOT EXISTS posts_status ON posts(status)")
            state.db.commit()
            service._require_write_allowed()
            if service.profile.base_url != state.get("server") or service.me().id != state.get(
                "user_id"
            ):
                raise UsageError("The current account or server does not match the approved plan")
            client = LimitedClient(service.client, gate)
            old_client = service.client
            service.client = client
            try:
                apply_membership(service, state)
                collect_posts(service, state)
                apply_reactions(service, state, workers=workers)
            finally:
                client.close()
                service.client = old_client
    return state.summary()


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="timetk-batch", description="Explicitly confirmed resumable recent-post reactions"
    )
    parser.add_argument("mode", choices=("plan", "apply", "status"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--folder")
    parser.add_argument("--emoji")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--channel-type", choices=("O", "P", "D", "G"), action="append")
    parser.add_argument(
        "--channel", action="append", help="exact channel ID; repeat for a bounded pilot"
    )
    parser.add_argument("--approve-run", default="")
    parser.add_argument("--confirm-plan", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--requests-per-second", type=float, default=8)
    args = parser.parse_args(argv)
    state = None
    try:
        if args.mode == "plan":
            if not args.profile or not args.folder or not args.emoji:
                raise UsageError("plan requires explicit --profile, --folder and --emoji")
            state = BatchState(args.state, create=True)
            with TimeService.open(args.profile) as service:
                result = create_plan(
                    service,
                    state,
                    emoji=args.emoji,
                    folder=args.folder,
                    limit=args.limit,
                    channel_types=args.channel_type,
                    channel_ids=args.channel,
                )
        else:
            state = BatchState(args.state)
            result = (
                state.summary()
                if args.mode == "status"
                else run_batch(
                    state,
                    approve_run=args.approve_run,
                    confirm_plan=args.confirm_plan,
                    workers=args.workers,
                    rate=args.requests_per_second,
                )
            )
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 1 if args.mode == "apply" and not result["complete"] else 0
    except TimeToolkitError as error:
        print(json.dumps({"error": error.message, "error_type": type(error).__name__}), flush=True)
        return int(error.exit_code)
    finally:
        if state is not None:
            state.close()


if __name__ == "__main__":
    raise SystemExit(main())
