import pytest

from time_toolkit.models import Post, primitive


def test_mention_is_derived_from_message_not_nonexistent_props_field():
    post = Post.from_api(
        {
            "id": "p" * 26,
            "channel_id": "c" * 26,
            "user_id": "u" * 26,
            "message": "Hello @student",
            "create_at": 1,
            "props": {},
        },
        base_url="https://time.example.test",
        current_username="student",
    )
    assert post.is_mention is True
    assert primitive(post)["permalink"].endswith("p" * 26)


def test_email_like_text_is_not_a_mention():
    post = Post.from_api(
        {
            "id": "p" * 26,
            "message": "student@example.com",
            "create_at": 1,
        },
        base_url="https://time.example.test",
        current_username="example",
    )
    assert post.is_mention is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (1, True),
        ("true", True),
        ("1", True),
        (False, False),
        (0, False),
        ("false", False),
        ("", False),
    ],
)
def test_post_exposes_realtime_metadata_without_copying_props(value, expected):
    post = Post.from_api(
        {
            "id": "p" * 26,
            "message": "Synthetic notification",
            "create_at": 1,
            "edit_at": 2,
            "delete_at": 3,
            "type": "custom_post_type",
            "props": {"from_bot": value, "private_extension": "not-copied"},
        },
        base_url="https://time.example.test",
    )
    payload = primitive(post)
    assert post.post_type == "custom_post_type"
    assert post.edit_at == 2
    assert post.delete_at == 3
    assert post.is_from_bot is expected
    assert "props" not in payload
    assert "private_extension" not in payload
