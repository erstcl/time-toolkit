"""Root-only selection and bounded reply expansion for an explicitly approved batch."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from time_toolkit.errors import AuthenticationError, NetworkError, TimeToolkitError, UsageError

if TYPE_CHECKING:
    from time_toolkit.batch import BatchState
    from time_toolkit.service import TimeService


def _store_post(state: BatchState, post: dict, channel_id: str, kind: str, root_id: str):
    identifier = post.get("id", "")
    if not re.fullmatch(r"[a-z0-9]{26}", identifier) or post.get("channel_id") != channel_id:
        raise UsageError("Thread post identity or channel does not match the approved scope")
    if kind == "root" and post.get("root_id"):
        raise UsageError("A channel root cannot be a reply")
    if kind == "reply" and (post.get("root_id") != root_id or identifier == root_id):
        raise UsageError("Reply does not belong to the selected root")
    metadata = post.get("metadata") or {}
    reactions = metadata.get("reactions")
    present = isinstance(reactions, list) and any(
        item.get("user_id") == state.get("user_id") and item.get("emoji_name") == state.get("emoji")
        for item in reactions
    )
    has_reactions = post.get("has_reactions")
    if has_reactions is None and reactions == []:
        has_reactions = False
    receipt = state.db.execute(
        "SELECT attempts FROM prior_receipts WHERE id=?", (identifier,)
    ).fetchone()
    attempts = max(1, receipt[0]) if receipt else 0
    state.db.execute(
        "INSERT OR IGNORE INTO posts(id,channel_id,has_reactions,status,attempts) "
        "VALUES(?,?,?,?,?)",
        (
            identifier,
            channel_id,
            int(has_reactions) if isinstance(has_reactions, bool) else None,
            "already_present" if present else "pending",
            attempts,
        ),
    )
    state.db.execute(
        "INSERT OR IGNORE INTO post_selection VALUES(?,?,?)", (identifier, kind, root_id)
    )


def index_roots(service: TimeService, state: BatchState, channel_id: str):
    marker = "roots_frozen:" + channel_id
    if state.get(marker):
        return
    raw_channel = service.client.get_channel(channel_id)
    if raw_channel.get("type") not in state.get("channel_types", ["O"]):
        raise UsageError("Channel type no longer matches the selected public scope")
    roots = {}
    seen_pages = set()
    for page in range(1000):
        rows = service.client.get_channel_roots_page(channel_id, page=page, per_page=100)
        signature = tuple(post["id"] for post in rows)
        if rows and signature in seen_pages:
            raise UsageError("Root pagination repeated a page")
        seen_pages.add(signature)
        for post in rows:
            if not post.get("delete_at"):
                roots[post["id"]] = post
        if len(roots) >= state.get("limit") or len(rows) < 100:
            break
    else:
        raise UsageError("Root pagination exceeded the safety bound")
    selected = sorted(
        roots.values(), key=lambda post: (post.get("create_at", 0), post["id"]), reverse=True
    )[: state.get("limit")]
    for post in selected:
        _store_post(state, post, channel_id, "root", post["id"])
        count = post.get("reply_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            count = None
        state.db.execute(
            "INSERT OR IGNORE INTO thread_roots VALUES(?,?,?,?)",
            (post["id"], channel_id, count, int(count == 0)),
        )
    state.put(marker, True)


def index_all_roots(service: TimeService, state: BatchState):
    rows = state.db.execute("SELECT id FROM channels ORDER BY rowid").fetchall()
    for index, row in enumerate(rows, 1):
        index_roots(service, state, row[0])
        if index % 50 == 0 or index == len(rows):
            print(
                json.dumps(
                    {"phase": "indexing_roots", "processed_channels": index, "total": len(rows)}
                ),
                flush=True,
            )
    counts = state.db.execute(
        "SELECT COUNT(*),SUM(CASE WHEN reply_count IS NULL THEN ? "
        "ELSE MIN(reply_count,?) END) "
        "FROM thread_roots",
        (state.get("thread_reply_limit"), state.get("thread_reply_limit")),
    ).fetchone()
    state.put("estimated_posts", counts[0] + (counts[1] or 0))
    state.put("root_volume", {"roots": counts[0], "estimated_replies": counts[1] or 0})
    return state.get("root_volume")


def collect_roots_and_replies(service: TimeService, state: BatchState):
    limit = state.get("thread_reply_limit")
    pending = state.db.execute(
        "SELECT id FROM channels WHERE joined=1 AND fetched=0 ORDER BY rowid"
    ).fetchall()
    for index, channel in enumerate(pending, 1):
        identifier = channel[0]
        try:
            index_roots(service, state, identifier)
            if service.client.get_channel(identifier).get("type") not in state.get(
                "channel_types", ["O"]
            ):
                raise UsageError("Channel type changed before reply expansion")
            roots = state.db.execute(
                "SELECT id FROM thread_roots WHERE channel_id=? AND replies_fetched=0 "
                "ORDER BY rowid",
                (identifier,),
            ).fetchall()
            with ThreadPoolExecutor(max_workers=4) as reader_pool:
                reads = {
                    reader_pool.submit(service.client.get_thread_tail, root[0], replies=limit): root
                    for root in roots
                }
                for read in as_completed(reads):
                    root = reads[read]
                    try:
                        posts = read.result()
                        replies = sorted(
                            (
                                post
                                for post in posts
                                if post.get("root_id") == root[0] and not post.get("delete_at")
                            ),
                            key=lambda post: (post.get("create_at", 0), post["id"]),
                            reverse=True,
                        )[:limit]
                        state.db.execute("SAVEPOINT one_thread")
                        try:
                            for post in replies:
                                _store_post(state, post, identifier, "reply", root[0])
                        except Exception:
                            state.db.execute("ROLLBACK TO one_thread")
                            state.db.execute("RELEASE one_thread")
                            raise
                        state.db.execute("RELEASE one_thread")
                        state.db.execute(
                            "UPDATE thread_roots SET replies_fetched=1 WHERE id=?", (root[0],)
                        )
                        state.db.commit()
                    except (AuthenticationError, NetworkError):
                        for queued in reads:
                            queued.cancel()
                        raise
                    except TimeToolkitError as error:
                        if error.status_code in {408, 425, 429, 500, 502, 503, 504}:
                            for queued in reads:
                                queued.cancel()
                            raise NetworkError(
                                "Temporary thread response; progress preserved"
                            ) from error
                        state.db.execute(
                            "UPDATE thread_roots SET replies_fetched=-1 WHERE id=?", (root[0],)
                        )
                        state.put("thread_error:" + root[0], error.message)
            failed = state.db.execute(
                "SELECT COUNT(*) FROM thread_roots WHERE channel_id=? AND replies_fetched=-1",
                (identifier,),
            ).fetchone()[0]
            state.db.execute(
                "UPDATE channels SET fetched=1,error=? WHERE id=?",
                (f"{failed} selected threads could not be fetched" if failed else "", identifier),
            )
            state.db.commit()
        except (AuthenticationError, NetworkError):
            raise
        except TimeToolkitError as error:
            state.db.execute(
                "UPDATE channels SET fetched=1,error=? WHERE id=?", (error.message, identifier)
            )
            state.db.commit()
        if index % 25 == 0 or index == len(pending):
            print(
                json.dumps(
                    {
                        "phase": "expanding_threads",
                        "processed_channels": index,
                        "total": len(pending),
                    }
                ),
                flush=True,
            )


def validate_thread_selection(state: BatchState):
    if not 1 <= state.get("thread_reply_limit", 0) <= 199:
        raise UsageError("Invalid approved thread reply limit")
    missing = state.db.execute(
        "SELECT COUNT(*) FROM posts p LEFT JOIN post_selection s ON s.post_id=p.id "
        "LEFT JOIN thread_roots r ON r.id=s.root_id "
        "WHERE s.post_id IS NULL OR r.id IS NULL OR r.channel_id!=p.channel_id "
        "OR s.kind NOT IN ('root','reply') OR (s.kind='root' AND p.id!=r.id) "
        "OR (s.kind='reply' AND p.id=r.id)"
    ).fetchone()[0]
    if missing:
        raise UsageError("Post selection is outside the approved root/thread scope")
    if state.db.execute(
        "SELECT r.id FROM thread_roots r LEFT JOIN post_selection s ON s.post_id=r.id "
        "LEFT JOIN posts p ON p.id=r.id "
        "WHERE s.kind IS NULL OR s.kind!='root' OR s.root_id!=r.id "
        "OR p.id IS NULL OR p.channel_id!=r.channel_id"
    ).fetchone():
        raise UsageError("A selected thread is missing its approved channel root")
    if state.db.execute(
        "SELECT channel_id FROM thread_roots GROUP BY channel_id HAVING COUNT(*)>?",
        (state.get("limit"),),
    ).fetchone():
        raise UsageError("Too many selected roots in a channel")
    if state.db.execute(
        "SELECT root_id FROM post_selection WHERE kind='reply' GROUP BY root_id HAVING COUNT(*)>?",
        (state.get("thread_reply_limit"),),
    ).fetchone():
        raise UsageError("Too many selected replies in a thread")


def derive_thread_plan(previous: BatchState, state: BatchState, *, reply_limit: int = 50):
    if not 1 <= reply_limit <= 199:
        raise UsageError("Reply limit must be between 1 and 199")
    if previous.get("channel_types") != ["O"] or previous.get("limit") != 75:
        raise UsageError("Expected the original approved public-75 scope")
    for row in previous.db.execute("SELECT * FROM channels ORDER BY rowid"):
        state.db.execute(
            "INSERT INTO channels(id,name,team_id,type,was_member,needs_join,joined,moved,"
            "fetched,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["name"],
                row["team_id"],
                row["type"],
                row["was_member"],
                row["needs_join"],
                row["joined"],
                row["moved"],
                0,
                "",
            ),
        )
    state.db.executemany(
        "INSERT INTO prior_receipts VALUES(?,?,?)",
        [
            (row["id"], row["status"], row["attempts"])
            for row in previous.db.execute("SELECT * FROM posts")
        ],
    )
    state.db.commit()
    for key in (
        "profile",
        "server",
        "user_id",
        "emoji",
        "limit",
        "channel_types",
        "folders",
        "sidebar_before",
    ):
        state.put(key, previous.get(key))
    state.put("run_id", uuid.uuid4().hex)
    state.put("selection_mode", "roots_and_replies")
    state.put("thread_reply_limit", reply_limit)
    state.put("prior_state", str(previous.path))
    state.put(
        "prior_completed",
        previous.db.execute(
            "SELECT COUNT(*) FROM posts WHERE status IN ('added','already_present')"
        ).fetchone()[0],
    )
    manifest = {
        key: state.get(key)
        for key in (
            "run_id",
            "profile",
            "server",
            "user_id",
            "emoji",
            "limit",
            "channel_types",
            "selection_mode",
            "thread_reply_limit",
        )
    }
    manifest["folders"] = {key: value["name"] for key, value in state.get("folders").items()}
    manifest["channels"] = [
        list(row)
        for row in state.db.execute(
            "SELECT id,team_id,type,was_member,needs_join FROM channels ORDER BY id"
        )
    ]
    state.put("approval_manifest", manifest)
    state.put(
        "approval_digest", hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    )
    return state.summary()
