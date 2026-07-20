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
