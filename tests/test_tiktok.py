from pathlib import Path

import httpx
import pytest

from shorts_bot.errors import UploadError, UploadLimitError
from shorts_bot.models import ShortPlan
from shorts_bot.tiktok import TikTokUploader


def _plan(title: str = "A title #Shorts") -> ShortPlan:
    return ShortPlan(0, 30, title, "Description", "Caption")


async def test_direct_post_flow_uploads_chunks_and_polls(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v2/post/publish/video/init/":
            assert request.headers["authorization"] == "Bearer tiktok-token"
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "pub-123",
                        "upload_url": "https://open-upload.put.example/video/",
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if request.url.path == "/video/":
            assert "bytes 0-" in request.headers["content-range"]
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/v2/post/publish/status/fetch/":
            return httpx.Response(
                200,
                json={
                    "data": {"status": "PUBLISH_COMPLETE"},
                    "error": {"code": "ok", "message": ""},
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video-bytes")
    uploader = TikTokUploader(
        access_token="tiktok-token",
        privacy_level="SELF_ONLY",
        poll_interval_seconds=1,
        processing_timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    publish_id = await uploader.upload(video_path, _plan())

    assert publish_id == "pub-123"
    init_body = requests[0].content
    assert b'"privacy_level":"SELF_ONLY"' in init_body
    assert requests[-1].url.path == "/v2/post/publish/status/fetch/"


async def test_tiktok_error_maps_to_upload_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {},
                "error": {"code": "scope_not_authorized", "message": "Scope not authorized"},
            },
        )

    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video-bytes")
    uploader = TikTokUploader(
        access_token="tiktok-token",
        poll_interval_seconds=1,
        processing_timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(UploadError, match="Scope not authorized"):
        await uploader.upload(video_path, _plan())


async def test_tiktok_rate_limit_maps_to_upload_limit_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {},
                "error": {"code": "rate_limit_exceeded", "message": "Too many requests"},
            },
        )

    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video-bytes")
    uploader = TikTokUploader(
        access_token="tiktok-token",
        poll_interval_seconds=1,
        processing_timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(UploadLimitError):
        await uploader.upload(video_path, _plan())


async def test_title_is_truncated_to_tiktok_limit(tmp_path: Path) -> None:
    captured: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/post/publish/video/init/":
            captured["init"] = request.content
            return httpx.Response(
                200,
                json={
                    "data": {
                        "publish_id": "pub-9",
                        "upload_url": "https://open-upload.put.example/video/",
                    },
                    "error": {"code": "ok", "message": ""},
                },
            )
        if request.url.path == "/video/":
            return httpx.Response(200, json={"success": True})
        if request.url.path == "/v2/post/publish/status/fetch/":
            return httpx.Response(
                200,
                json={"data": {"status": "PUBLISH_COMPLETE"}, "error": {"code": "ok"}},
            )
        raise AssertionError("unexpected")

    video_path = tmp_path / "short.mp4"
    video_path.write_bytes(b"video-bytes")
    uploader = TikTokUploader(
        access_token="tiktok-token",
        poll_interval_seconds=1,
        processing_timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    await uploader.upload(video_path, _plan("x" * 300))

    assert b'"title":"' + b"x" * 150 + b'"' in captured["init"]


def test_tiktok_requires_token() -> None:
    with pytest.raises(UploadError):
        TikTokUploader(access_token="   ")
