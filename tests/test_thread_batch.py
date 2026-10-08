from __future__ import annotations

import pytest

from time_toolkit.batch import BatchState
from time_toolkit.config import Profile
from time_toolkit.errors import NetworkError, NotFoundError, TimeToolkitError, UsageError
from time_toolkit.service import TimeService
from time_toolkit.thread_batch import (
    collect_roots_and_replies,
    index_all_roots,
    validate_thread_selection,
)

CHANNEL = "c" * 26
ROOT_A, ROOT_B = "a" * 26, "b" * 26


def post(identifier, created, root="", **extra):
    return {
        "id": identifier,
        "channel_id": CHANNEL,
        "root_id": root,
        "create_at": created,
        "reply_count": 0,
        "has_reactions": False,
        "message": "Synthetic body not to be persisted",
        **extra,
    }


class Client:
    def __init__(self):
        self.root_pages = [[post(ROOT_A, 10, reply_count=60), post(ROOT_B, 5)]]
        self.thread_calls = []

    def get_channel(self, identifier):
        return {"id": identifier, "type": "O"}

    def get_channel_roots_page(self, _identifier, *, page, per_page):
        return self.root_pages[page] if page < len(self.root_pages) else []

    def get_thread_tail(self, root_id, *, replies):
        self.thread_calls.append((root_id, replies))
        return [post(ROOT_A, 10)] + [
            post("r" + str(index).zfill(25), index + 20, ROOT_A) for index in range(60)
        ]


def state(tmp_path):
    selected = BatchState(tmp_path / "threads.sqlite", create=True)
    for key, value in {
        "limit": 75,
        "thread_reply_limit": 50,
        "selection_mode": "roots_and_replies",
        "channel_types": ["O"],
        "user_id": "u" * 26,
        "emoji": "test",
    }.items():
        selected.put(key, value)
    selected.db.execute(
        "INSERT INTO channels(id,name,team_id,type,was_member,needs_join,joined,moved) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (CHANNEL, "Synthetic study", "t" * 26, "O", 1, 0, 1, 0),
    )
    selected.db.commit()
    return selected


def test_root_and_reply_limits_and_resume_are_independent(tmp_path):
    selected = state(tmp_path)
    client = Client()
    service = TimeService(Profile("test", "https://time.example.test"), client)
    try:
        volume = index_all_roots(service, selected)
        assert volume == {"roots": 2, "estimated_replies": 50}
        collect_roots_and_replies(service, selected)
        validate_thread_selection(selected)
        assert selected.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 52
        replies = {
            row[0]
            for row in selected.db.execute("SELECT post_id FROM post_selection WHERE kind='reply'")
        }
        assert replies == {"r" + str(index).zfill(25) for index in range(10, 60)}
        assert client.thread_calls == [(ROOT_A, 50)]
        collect_roots_and_replies(service, selected)
        assert client.thread_calls == [(ROOT_A, 50)]
        assert b"Synthetic body" not in selected.path.read_bytes()
    finally:
        selected.close()


def test_prior_unknown_attempt_is_preserved_and_existing_own_reaction_is_skipped(tmp_path):
    selected = state(tmp_path)
    client = Client()
    client.root_pages[0][0]["metadata"] = {
        "reactions": [{"user_id": "u" * 26, "emoji_name": "test"}]
    }
    selected.db.execute("INSERT INTO prior_receipts VALUES(?,?,?)", (ROOT_B, "pending", 2))
    selected.db.commit()
    try:
        index_all_roots(TimeService(Profile("test", "https://time.example.test"), client), selected)
        assert (
            selected.db.execute("SELECT status FROM posts WHERE id=?", (ROOT_A,)).fetchone()[0]
            == "already_present"
        )
        assert (
            selected.db.execute("SELECT attempts FROM posts WHERE id=?", (ROOT_B,)).fetchone()[0]
            == 2
        )
    finally:
        selected.close()


def test_thread_limit_tampering_is_rejected(tmp_path):
    selected = state(tmp_path)
    service = TimeService(Profile("test", "https://time.example.test"), Client())
    try:
        collect_roots_and_replies(service, selected)
        selected.put("thread_reply_limit", 49)
        with pytest.raises(UsageError, match="Too many selected replies"):
            validate_thread_selection(selected)
    finally:
        selected.close()


def test_missing_thread_does_not_skip_healthy_threads(tmp_path):
    selected = state(tmp_path)
    client = Client()
    client.root_pages[0][1]["reply_count"] = 1

    def tail(root_id, *, replies):
        if root_id == ROOT_A:
            raise NotFoundError("Synthetic missing thread")
        return [post("r" * 26, 20, ROOT_B)]

    client.get_thread_tail = tail
    try:
        collect_roots_and_replies(
            TimeService(Profile("test", "https://time.example.test"), client), selected
        )
        assert (
            selected.db.execute(
                "SELECT replies_fetched FROM thread_roots WHERE id=?", (ROOT_A,)
            ).fetchone()[0]
            == -1
        )
        assert (
            selected.db.execute("SELECT status FROM posts WHERE id=?", ("r" * 26,)).fetchone()[0]
            == "pending"
        )
        assert selected.db.execute("SELECT error FROM channels").fetchone()[0]
        validate_thread_selection(selected)
    finally:
        selected.close()


def test_temporary_thread_response_remains_retryable(tmp_path):
    selected = state(tmp_path)
    client = Client()

    def tail(root_id, *, replies):
        raise TimeToolkitError("Synthetic overload", status_code=503)

    client.get_thread_tail = tail
    try:
        with pytest.raises(NetworkError, match="progress preserved"):
            collect_roots_and_replies(
                TimeService(Profile("test", "https://time.example.test"), client), selected
            )
        assert (
            selected.db.execute(
                "SELECT replies_fetched FROM thread_roots WHERE id=?", (ROOT_A,)
            ).fetchone()[0]
            == 0
        )
        assert selected.db.execute("SELECT fetched FROM channels").fetchone()[0] == 0
    finally:
        selected.close()
