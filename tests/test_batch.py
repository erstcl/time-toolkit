from __future__ import annotations

import copy
import json
import threading
from collections import Counter

import pytest

from time_toolkit.batch import (
    BatchState,
    apply_membership,
    apply_reactions,
    collect_posts,
    create_plan,
    react_one,
    run_batch,
)
from time_toolkit.config import Profile
from time_toolkit.errors import ConflictError, NetworkError, PermissionError, UsageError
from time_toolkit.service import TimeService

USER, TEAM, OLD, NEW, DM, EMPTY, CATEGORY = (letter * 26 for letter in "utondex")
EMOJI = "approved_test_emoji"


def channel(identifier, kind="O", count=2, name="Study"):
    return {
        "id": identifier,
        "team_id": TEAM,
        "type": kind,
        "name": name,
        "display_name": name,
        "total_msg_count": count,
    }


def category(identifier, name, channels):
    return {
        "id": identifier,
        "type": "custom",
        "display_name": name,
        "user_id": USER,
        "team_id": TEAM,
        "channel_ids": channels,
        "muted": True,
        "extra": {"preserved": True},
    }


class Client:
    def __init__(self):
        self.members = [channel(OLD), channel(DM, "D")]
        self.public = [channel(OLD), channel(NEW, name="BSc NLP"), channel(EMPTY, count=0)]
        self.categories = [category("source", "Existing", [OLD, DM])]
        self.calls = Counter()
        self.reactions = {}

    def get_me(self):
        return {"id": USER, "username": "student"}

    def get_my_teams(self):
        return [{"id": TEAM, "name": "study"}]

    def request(self, verb, _path, **_kwargs):
        assert verb == "GET"
        if "/emoji/name/" in _path:
            return {"name": EMOJI, "id": "emoji-id", "delete_at": 0}
        return self.members

    def get_public_channels_page(self, _team, **_kwargs):
        return self.public

    def get_sidebar_categories(self, _user, _team):
        return {"categories": copy.deepcopy(self.categories), "order": ["source"]}

    def get_channel(self, identifier):
        return next(row for row in self.members + self.public if row["id"] == identifier)

    def join_channel(self, user, identifier, **_kwargs):
        assert user == USER and identifier == NEW
        self.calls["join"] += 1
        self.members.append(self.get_channel(identifier))

    def create_sidebar_category(self, user, team, name, **_kwargs):
        assert user == USER and team == TEAM
        self.calls["create_category"] += 1
        row = category(CATEGORY, name, [])
        self.categories.append(row)
        return row

    def update_sidebar_categories(self, _user, _team, rows, **_kwargs):
        self.calls["update_categories"] += 1
        self.categories = rows

    def get_channel_posts_page(self, identifier, *, per_page):
        self.calls["fetch_posts"] += 1
        prefix = {OLD: "a", NEW: "b", DM: "c"}[identifier]
        return [
            {
                "id": prefix + str(index).zfill(25),
                "channel_id": identifier,
                "create_at": index,
                "message": "Synthetic text not to be stored",
                "has_reactions": False,
            }
            for index in range(per_page)
        ]

    def get_post_reactions(self, identifier):
        self.calls["get_reactions"] += 1
        return self.reactions.get(identifier, [])

    def add_reaction(self, user, identifier, emoji, **_kwargs):
        self.calls["add_reaction"] += 1
        self.reactions.setdefault(identifier, []).append({"user_id": user, "emoji_name": emoji})


def planned(tmp_path, client=None):
    client = client or Client()
    service = TimeService(
        Profile("test", "https://time.example.test", team_id=TEAM, write_policy="approval"),
        client,
        write_mode="confirmed",
    )
    state = BatchState(tmp_path / "batch.sqlite", create=True)
    create_plan(service, state, emoji=EMOJI, folder="New channels", limit=2)
    return service, state, client


def test_plan_includes_bsc_and_member_dms_without_writes_or_message_reads(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        summary = state.summary()
        assert summary["channels"] == 3
        assert summary["new_channels"] == 1
        assert summary["estimated_posts"] == 6
        assert client.calls == Counter()
        assert {row[0] for row in state.db.execute("SELECT id FROM channels")} == {OLD, NEW, DM}
        assert (tmp_path / "batch.plan.md").is_file()
    finally:
        state.close()


def test_public_only_plan_excludes_private_direct_and_group_conversations(tmp_path):
    client = Client()
    client.members.extend([channel("p" * 26, "P"), channel("g" * 26, "G")])
    service = TimeService(Profile("test", "https://time.example.test", team_id=TEAM), client)
    state = BatchState(tmp_path / "public.sqlite", create=True)
    try:
        create_plan(service, state, emoji=EMOJI, folder="New", limit=75, channel_types=["O"])
        rows = state.db.execute("SELECT id,type FROM channels").fetchall()
        assert {row[0] for row in rows} == {OLD, NEW}
        assert {row[1] for row in rows} == {"O"}
        assert state.get("approval_manifest")["channel_types"] == ["O"]
        assert client.calls == Counter()
    finally:
        state.close()


def test_bounded_pilot_selects_only_explicit_channel_ids(tmp_path):
    client = Client()
    service = TimeService(Profile("test", "https://time.example.test", team_id=TEAM), client)
    state = BatchState(tmp_path / "pilot.sqlite", create=True)
    try:
        create_plan(
            service,
            state,
            emoji=EMOJI,
            folder="New",
            limit=75,
            channel_types=["O"],
            channel_ids=[NEW],
        )
        assert state.summary()["channels"] == 1
        assert state.summary()["new_channels"] == 1
        assert state.db.execute("SELECT id FROM channels").fetchone()[0] == NEW
        assert client.calls == Counter()
    finally:
        state.close()


def test_channel_that_becomes_private_is_not_read_for_public_only_run(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        state.put("channel_types", ["O"])
        client.members[0]["type"] = "P"
        collect_posts(service, state)
        assert (
            state.db.execute("SELECT COUNT(*) FROM posts WHERE channel_id=?", (OLD,)).fetchone()[0]
            == 0
        )
        assert state.db.execute("SELECT error FROM channels WHERE id=?", (OLD,)).fetchone()[0]
    finally:
        state.close()


def test_only_new_memberships_move_and_resume_does_not_duplicate_changes(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        apply_membership(service, state)
        assert client.categories[0]["channel_ids"] == [OLD, DM]
        assert client.categories[0]["extra"] == {"preserved": True}
        assert client.categories[1]["channel_ids"] == [NEW]
        assert client.calls == Counter(join=1, create_category=1, update_categories=1)
        apply_membership(service, state)
        assert client.calls == Counter(join=1, create_category=1, update_categories=1)
    finally:
        state.close()


def test_each_join_is_filed_before_the_next_join_and_move_failure_stops_batch(tmp_path):
    client = Client()
    second = "z" * 26
    client.public.append(channel(second))
    service, state, _client = planned(tmp_path, client)
    events = []
    move_keys = []
    fail_move = [True]
    original_move = client.update_sidebar_categories

    def join(user, identifier, **_kwargs):
        assert user == USER
        events.append(("join", identifier))
        client.members.append(client.get_channel(identifier))

    def move(user, team, rows, **kwargs):
        move_keys.append(kwargs["idempotency_key"])
        if fail_move[0]:
            raise NetworkError("Synthetic folder failure")
        original_move(user, team, rows)
        events.append(("move", rows[-1]["channel_ids"][-1]))

    client.join_channel = join
    client.update_sidebar_categories = move
    try:
        with pytest.raises(NetworkError):
            apply_membership(service, state)
        assert events == [("join", NEW)]
        assert (
            state.db.execute("SELECT joined,moved FROM channels WHERE id=?", (NEW,)).fetchone()[
                "moved"
            ]
            == 0
        )
        assert (
            state.db.execute("SELECT joined FROM channels WHERE id=?", (second,)).fetchone()[0] == 0
        )
        fail_move[0] = False
        apply_membership(service, state)
        assert events == [("join", NEW), ("move", NEW), ("join", second), ("move", second)]
        assert move_keys[0] == move_keys[1]
        assert move_keys[1] != move_keys[2]
        assert (
            state.db.execute(
                "SELECT COUNT(*) FROM channels WHERE needs_join=1 AND moved=1"
            ).fetchone()[0]
            == 2
        )
    finally:
        state.close()


def test_recent_ids_are_durable_but_message_bodies_are_not_stored(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        apply_membership(service, state)
        collect_posts(service, state)
        assert state.db.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 6
        collect_posts(service, state)
        assert client.calls["fetch_posts"] == 3
        assert b"Synthetic text not to be stored" not in state.path.read_bytes()
    finally:
        state.close()


def test_parallel_reaction_progress_and_second_run_are_noop(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        apply_membership(service, state)
        collect_posts(service, state)
        apply_reactions(service, state, workers=2)
        assert state.summary()["posts"] == {"added": 6}
        assert client.calls["add_reaction"] == 6
        apply_reactions(service, state, workers=2)
        assert client.calls["add_reaction"] == 6
    finally:
        state.close()


def test_slow_request_does_not_block_all_other_queued_posts(tmp_path, monkeypatch):
    service, selected, client = planned(tmp_path)
    try:
        apply_membership(service, selected)
        collect_posts(service, selected)
        ids = [row[0] for row in selected.db.execute("SELECT id FROM posts ORDER BY rowid")]
        last_fast = threading.Event()

        def reaction(_client, row, **_kwargs):
            if row["id"] == ids[0]:
                assert last_fast.wait(2), "All other records were blocked behind one slow request"
            elif row["id"] == ids[-1]:
                last_fast.set()
            return "added"

        monkeypatch.setattr("time_toolkit.batch.react_one", reaction)
        apply_reactions(service, selected, workers=2)
        assert selected.summary()["posts"] == {"added": 6}
    finally:
        selected.close()


def test_temporary_failure_stays_pending_and_uncertain_post_is_checked_on_resume(
    tmp_path, monkeypatch
):
    service, selected, client = planned(tmp_path)
    try:
        apply_membership(service, selected)
        collect_posts(service, selected)
        identifier = selected.db.execute("SELECT id FROM posts ORDER BY rowid LIMIT 1").fetchone()[
            0
        ]
        original = client.add_reaction

        def uncertain(user, post_id, emoji, **kwargs):
            original(user, post_id, emoji, **kwargs)
            if post_id == identifier:
                raise NetworkError("Synthetic lost acknowledgement")

        client.add_reaction = uncertain
        with pytest.raises(NetworkError):
            apply_reactions(service, selected, workers=1)
        assert (
            selected.db.execute(
                "SELECT status,attempts FROM posts WHERE id=?", (identifier,)
            ).fetchone()[0]
            == "pending"
        )
        client.add_reaction = original
        apply_reactions(service, selected, workers=1)
        assert selected.summary()["posts"] == {"added": 5, "already_present": 1}
        assert len(client.reactions[identifier]) == 1
    finally:
        selected.close()


def test_uncertain_previous_attempt_checks_existing_reaction_before_posting():
    client = Client()
    identifier = "p" * 26
    client.reactions[identifier] = [{"user_id": USER, "emoji_name": EMOJI}]
    result = react_one(
        client,
        {"id": identifier, "attempts": 1, "has_reactions": False},
        user_id=USER,
        emoji=EMOJI,
        run_id="test",
    )
    assert result == "already_present"
    assert client.calls["add_reaction"] == 0


def test_duplicate_reaction_conflict_rechecks_current_membership():
    client = Client()
    identifier = "p" * 26

    def conflict(*_args, **_kwargs):
        client.reactions[identifier] = [{"user_id": USER, "emoji_name": EMOJI}]
        raise ConflictError("Already exists")

    client.add_reaction = conflict
    assert (
        react_one(
            client,
            {"id": identifier, "attempts": 0, "has_reactions": False},
            user_id=USER,
            emoji=EMOJI,
            run_id="test",
        )
        == "already_present"
    )


def test_both_confirmation_values_and_immutable_scope_are_required(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        with pytest.raises(UsageError, match="Both exact"):
            run_batch(
                state, approve_run=state.get("run_id"), confirm_plan="wrong", workers=1, rate=8
            )
        state.db.execute("UPDATE channels SET needs_join=0 WHERE id=?", (NEW,))
        state.db.commit()
        with pytest.raises(UsageError, match="channel list changed"):
            run_batch(
                state,
                approve_run=state.get("run_id"),
                confirm_plan=state.get("approval_digest"),
                workers=1,
                rate=8,
            )
        assert client.calls == Counter()
    finally:
        state.close()


def test_readonly_blocks_batch_before_writes(tmp_path, monkeypatch):
    service, state, client = planned(tmp_path)
    service.profile = Profile(
        "test", "https://time.example.test", team_id=TEAM, write_policy="readonly"
    )
    client.close = lambda: None
    monkeypatch.setattr(TimeService, "open", lambda *_args, **_kwargs: service)
    try:
        with pytest.raises(PermissionError):
            run_batch(
                state,
                approve_run=state.get("run_id"),
                confirm_plan=state.get("approval_digest"),
                workers=1,
                rate=8,
            )
        assert client.calls == Counter()
    finally:
        state.close()


def test_resume_state_contains_no_authentication_values(tmp_path):
    service, state, client = planned(tmp_path)
    state.close()
    reopened = BatchState(tmp_path / "batch.sqlite")
    try:
        assert reopened.summary()["profile"] == "test"
        assert reopened.path.stat().st_mode & 0o777 == 0o600
        values = json.dumps(
            [json.loads(row[0]) for row in reopened.db.execute("SELECT value FROM meta")]
        )
        assert "authorization" not in values.lower()
        assert "cookie" not in values.lower()
    finally:
        reopened.close()


def test_complete_batch_keeps_existing_folders_and_resumes_without_more_posts(
    tmp_path, monkeypatch
):
    service, state, client = planned(tmp_path)
    client.close = lambda: None
    monkeypatch.setattr(TimeService, "open", lambda *_args, **_kwargs: service)
    monkeypatch.setattr("time_toolkit.batch.LimitedClient", lambda source, _gate: source)
    try:
        options = dict(
            approve_run=state.get("run_id"),
            confirm_plan=state.get("approval_digest"),
            workers=2,
            rate=8,
        )
        result = run_batch(state, **options)
        assert result["posts"] == {"added": 6}
        assert client.categories[0]["channel_ids"] == [OLD, DM]
        assert client.categories[1]["channel_ids"] == [NEW]
        assert run_batch(state, **options)["posts"] == {"added": 6}
        assert client.calls["add_reaction"] == 6
    finally:
        state.close()


def test_existing_process_lock_prevents_duplicate_batch(tmp_path):
    fcntl = pytest.importorskip("fcntl")
    service, state, client = planned(tmp_path)
    lock_path = state.path.with_suffix(".sqlite.lock")
    try:
        with lock_path.open("wb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(UsageError, match="already running"):
                run_batch(
                    state,
                    approve_run=state.get("run_id"),
                    confirm_plan=state.get("approval_digest"),
                    workers=1,
                    rate=8,
                )
        assert client.calls == Counter()
    finally:
        state.close()


def test_post_progress_cannot_target_unapproved_channel(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        state.db.execute("INSERT INTO posts(id,channel_id) VALUES(?,?)", ("p" * 26, "outside"))
        state.db.commit()
        with pytest.raises(UsageError, match="outside the approved"):
            run_batch(
                state,
                approve_run=state.get("run_id"),
                confirm_plan=state.get("approval_digest"),
                workers=1,
                rate=8,
            )
        assert client.calls == Counter()
    finally:
        state.close()


def test_summary_does_not_report_failed_reactions_as_complete(tmp_path):
    service, state, client = planned(tmp_path)
    try:
        assert state.summary()["complete"] is False
        apply_membership(service, state)
        collect_posts(service, state)
        apply_reactions(service, state, workers=1)
        assert state.summary()["complete"] is True
        state.db.execute("UPDATE posts SET status='failed' WHERE rowid=1")
        state.db.commit()
        assert state.summary()["complete"] is False
    finally:
        state.close()
