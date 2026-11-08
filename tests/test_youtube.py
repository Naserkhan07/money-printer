import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from shorts_bot.errors import UploadError
from shorts_bot.models import ShortPlan
from shorts_bot.youtube import (
    YouTubeUploader,
    _is_youtube_upload_limit,
    _shorts_description,
    _shorts_title,
)


class FakeChannelRequest:
    def __init__(self, channel_ids: list[str]) -> None:
        self.channel_ids = channel_ids

    def list(self, **kwargs):  # noqa: ANN003, ANN201
        assert kwargs == {"part": "id", "mine": True}
        return self

    def execute(self):  # noqa: ANN201
        return {"items": [{"id": channel_id} for channel_id in self.channel_ids]}


class FakeYouTube:
    def __init__(self, channel_ids: list[str]) -> None:
        self.request = FakeChannelRequest(channel_ids)

    def channels(self) -> FakeChannelRequest:
        return self.request


def test_adds_shorts_metadata_within_youtube_limits() -> None:
    assert _shorts_title("A useful title") == "A useful title #Shorts"
    assert _shorts_title("A useful title #Shorts") == "A useful title #Shorts"
    assert len(_shorts_title("x" * 150)) <= 100

    assert _shorts_description("A factual description").endswith("#Shorts")
    assert _shorts_description("A factual description #Shorts").count("#Shorts") == 1
    assert len(_shorts_description("x" * 6000)) <= 5000


def test_verifies_oauth_channel_matches_configuration() -> None:
    uploader = YouTubeUploader(Path("token.json"), expected_channel_id="UC-correct")

    uploader._verify_channel(FakeYouTube(["UC-correct"]))

    with pytest.raises(UploadError, match="channels.toml specifies UC-correct"):
        uploader._verify_channel(FakeYouTube(["UC-wrong"]))


def test_detects_youtube_daily_upload_limit_reason() -> None:
    content = json.dumps(
        {
            "error": {
                "errors": [
                    {
                        "reason": "uploadLimitExceeded",
                        "message": "The user has exceeded the number of videos they may upload.",
                    }
                ]
            }
        }
    ).encode()
    error = HttpError(Response({"status": "403", "reason": "Forbidden"}), content)

    assert _is_youtube_upload_limit(error, "uploadLimitExceeded")


class _FakeInsertRequest:
    def __init__(self, body: dict) -> None:
        self.body = body

    def next_chunk(self):  # noqa: ANN201
        return None, {"id": "vid-scheduled-1"}


class _FakeVideos:
    def __init__(self) -> None:
        self.insert_body: dict | None = None

    def insert(self, part: str, notifySubscribers: bool, body: dict, media_body):  # noqa: N803, ANN001, ANN201
        self.insert_body = body
        return _FakeInsertRequest(body)


class _FakeYouTubeWithUpload:
    def __init__(self, channel_id: str = "UC-channel") -> None:
        self._channels = FakeChannelRequest([channel_id])
        self._videos = _FakeVideos()

    def channels(self) -> FakeChannelRequest:
        return self._channels

    def videos(self) -> _FakeVideos:
        return self._videos


def test_publish_at_creates_private_video_with_schedule(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    service = _FakeYouTubeWithUpload()
    monkeypatch.setattr(
        "shorts_bot.youtube.build",
        lambda *args, **kwargs: service,
    )
    uploader = YouTubeUploader(Path("token.json"), privacy_status="public")
    monkeypatch.setattr(YouTubeUploader, "_credentials", lambda self: object())

    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video")
    plan = ShortPlan(0, 30, "Title", "Description", "Caption")
    publish_at = datetime.now(UTC) + timedelta(minutes=45)

    video_id = uploader._upload_sync(video_path, plan, None, publish_at)

    assert video_id == "vid-scheduled-1"
    status = service._videos.insert_body["status"]
    assert status["privacyStatus"] == "private"
    assert status["publishAt"].endswith("Z")


def test_publish_at_in_the_past_is_rejected(tmp_path: Path) -> None:
    from shorts_bot.youtube import _rfc3339_utc

    with pytest.raises(UploadError, match="not in the future"):
        _rfc3339_utc(datetime.now(UTC) - timedelta(minutes=1))


def test_immediate_upload_stays_public(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    service = _FakeYouTubeWithUpload()
    monkeypatch.setattr("shorts_bot.youtube.build", lambda *args, **kwargs: service)
    uploader = YouTubeUploader(Path("token.json"), privacy_status="public")
    monkeypatch.setattr(YouTubeUploader, "_credentials", lambda self: object())

    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video")
    plan = ShortPlan(0, 30, "Title", "Description", "Caption")

    uploader._upload_sync(video_path, plan)

    status = service._videos.insert_body["status"]
    assert status["privacyStatus"] == "public"
    assert "publishAt" not in status
