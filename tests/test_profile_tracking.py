import pytest

from aicraft import profile_tracking
from aicraft.db.models import InstagramProfileSnapshot, TrackedInstagramProfile


class FakeInfo:
    pk = 123
    follower_count = 1000
    following_count = 120
    media_count = 42
    full_name = "Ruby"
    is_private = False


class FakeMedia:
    def __init__(self, code, play_count, like_count=0, comment_count=0, caption_text=""):
        self.media_type = 2
        self.product_type = "clips"
        self.code = code
        self.play_count = play_count
        self.like_count = like_count
        self.comment_count = comment_count
        self.caption_text = caption_text


class FakeClient:
    def __init__(self):
        self.followers = 1000

    def user_info_by_username(self, username):
        info = FakeInfo()
        info.follower_count = self.followers
        return info

    def user_medias(self, user_id, amount):
        return [
            FakeMedia("LOW", 10, like_count=1),
            FakeMedia("BEST", 999, like_count=50, comment_count=7, caption_text="best caption"),
        ]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rubywilde", "rubywilde"),
        ("@RubyWilde", "rubywilde"),
        ("instagram.com/RubyWilde/", "rubywilde"),
        ("https://www.instagram.com/RubyWilde/?hl=it", "rubywilde"),
    ],
)
def test_username_from_url_accetta_profili(value, expected):
    assert profile_tracking.username_from_url(value) == expected


def test_username_from_url_rifiuta_post_o_reel():
    with pytest.raises(ValueError):
        profile_tracking.username_from_url("https://www.instagram.com/reel/ABC123/")


def test_add_sync_report_tracking_profile(db_session):
    tracked = profile_tracking.add_tracked_profile(
        db_session,
        url_or_username="https://www.instagram.com/RubyWilde/",
        label="Ruby Wilde",
    )
    db_session.commit()

    client = FakeClient()
    first = profile_tracking.sync_all(db_session, client=client)
    client.followers = 1015
    second = profile_tracking.sync_all(db_session, client=client)
    db_session.commit()

    assert first["errors"] == []
    assert second["errors"] == []
    assert db_session.query(TrackedInstagramProfile).one().username == "rubywilde"
    assert db_session.query(InstagramProfileSnapshot).count() == 2

    report = profile_tracking.report(db_session)
    row = report["profiles"][0]
    assert row["label"] == "Ruby Wilde"
    assert row["latest"]["followers_count"] == 1015
    assert row["followers_delta"] == 15
    assert row["latest"]["best_video_url"] == "https://www.instagram.com/reel/BEST/"
    assert row["latest"]["best_video_metric"] == 999


def test_deactivate_tracked_profile(db_session):
    tracked = profile_tracking.add_tracked_profile(db_session, url_or_username="ruby")
    db_session.commit()

    profile_tracking.deactivate_tracked_profile(db_session, tracked.id)
    db_session.commit()

    assert db_session.get(TrackedInstagramProfile, tracked.id).active is False
