"""TikTok Direct Post via the Content Posting API.

Adapted from the MIT-licensed social-media-scheduler by HillzKillzIt
(https://github.com/HillzKillzIt/social-media-scheduler) and rewritten for this
codebase: async ``httpx``, shared error types, and the official Direct Post
endpoints. See NOTICE.md for attribution.

Docs:
  https://developers.tiktok.com/doc/content-posting-api-reference-direct-post
  https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide

Requirements:
  * A TikTok developer app with the ``video.publish`` scope (direct posting)
    and the ``user.info.basic`` scope for the credential check.
  * Apps that have not passed TikTok's audit can only post with
    ``TIKTOK_PRIVACY_LEVEL=SELF_ONLY``. Set PUBLIC_TO_EVERYONE only after
    your app is approved for public content.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from .errors import UploadError, UploadLimitError
from .models import ShortPlan

logger = logging.getLogger(__name__)

TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
CHUNK_SIZE = 10 * 1024 * 1024  # 10 MB chunks (5–64 MB allowed by TikTok)
MIN_CHUNK_SIZE = 5 * 1024 * 1024
TIKTOK_TITLE_MAX_CHARS = 150


class TikTokUploader:
    """Upload local MP4 files to TikTok using Direct Post."""

    def __init__(
        self,
        access_token: str,
        privacy_level: str = "SELF_ONLY",
        api_base: str = TIKTOK_API_BASE,
        poll_interval_seconds: int = 10,
        processing_timeout_seconds: int = 600,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.access_token = access_token.strip()
        if not self.access_token:
            raise UploadError("TIKTOK_ACCESS_TOKEN is required.")
        self.privacy_level = privacy_level
        self.api_base = api_base.rstrip("/")
        self.poll_interval_seconds = max(1, poll_interval_seconds)
        self.processing_timeout_seconds = max(30, processing_timeout_seconds)
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    async def check_connection(self) -> str:
        timeout = httpx.Timeout(30, read=60)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            try:
                response = await client.get(
                    f"{self.api_base}/user/info/?fields=display_name,open_id",
                    headers=self._headers(),
                )
            except httpx.HTTPError as exc:
                raise UploadError("TikTok user info network request failed.") from exc
        payload = self._json(response, "TikTok user info")
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").casefold()
            if code and code != "ok":
                raise UploadError(
                    f"TikTok credential check failed: {error.get('message') or code}. "
                    "Make sure the token includes the user.info.basic scope."
                )
        data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        user = data.get("user", {}) if isinstance(data.get("user"), dict) else {}
        return str(user.get("display_name") or user.get("open_id") or "TikTok account")

    async def upload(self, video_path: Path, plan: ShortPlan) -> str:
        """Direct-post *video_path*. Returns the TikTok publish_id."""
        if not video_path.exists():
            raise UploadError(f"Rendered TikTok video does not exist: {video_path}")

        file_size = video_path.stat().st_size
        if file_size <= 0:
            raise UploadError("Rendered TikTok video is empty.")
        chunk_size = max(MIN_CHUNK_SIZE, CHUNK_SIZE) if file_size >= MIN_CHUNK_SIZE else file_size
        chunk_count = max(1, -(-file_size // chunk_size))

        post_info = {
            "title": plan.title[:TIKTOK_TITLE_MAX_CHARS],
            "privacy_level": self.privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        }
        init_payload = {
            "post_info": post_info,
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            },
        }

        timeout = httpx.Timeout(60, read=300)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            init = await self._post_json(
                client,
                f"{self.api_base}/post/publish/video/init/",
                operation="TikTok upload initialization",
                json_body=init_payload,
            )
            init_data = init.get("data", {}) if isinstance(init.get("data"), dict) else {}
            publish_id = str(init_data.get("publish_id") or "")
            upload_url = str(init_data.get("upload_url") or "")
            if not publish_id or not upload_url:
                raise UploadError("TikTok returned no publish_id or upload_url.")

            await self._upload_chunks(client, upload_url, video_path, file_size, chunk_size)
            await self._wait_for_publish(client, publish_id)
        return publish_id

    async def _upload_chunks(
        self,
        client: httpx.AsyncClient,
        upload_url: str,
        video_path: Path,
        file_size: int,
        chunk_size: int,
    ) -> None:
        with video_path.open("rb") as handle:
            start = 0
            index = 0
            while start < file_size:
                data = handle.read(chunk_size)
                if not data:
                    break
                end = start + len(data) - 1
                response = await client.put(
                    upload_url,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                        "Content-Length": str(len(data)),
                        "Content-Type": "video/mp4",
                    },
                    content=data,
                )
                if not response.is_success:
                    raise UploadError(
                        f"TikTok chunk upload failed at offset {start}: "
                        f"HTTP {response.status_code}: {response.text[:300]}"
                    )
                index += 1
                start = end + 1
        logger.info("TikTok: uploaded %d chunk(s) (%d bytes)", index, file_size)

    async def _wait_for_publish(self, client: httpx.AsyncClient, publish_id: str) -> None:
        attempts = max(1, self.processing_timeout_seconds // self.poll_interval_seconds)
        for _ in range(attempts):
            payload = await self._post_json(
                client,
                f"{self.api_base}/post/publish/status/fetch/",
                operation="TikTok publish status",
                json_body={"publish_id": publish_id},
            )
            data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            status = str(data.get("status") or "").upper()
            if status == "PUBLISH_COMPLETE":
                logger.info("TikTok: publish_id=%s completed", publish_id)
                return
            if status in {"FAILED", "PUBLISH_FAILED"}:
                reason = data.get("fail_reason") or data.get("reason") or status
                raise UploadError(f"TikTok publish failed: {reason}")
            await asyncio.sleep(self.poll_interval_seconds)
        raise UploadError("TikTok publish did not complete before the timeout.")

    async def _post_json(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        operation: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await client.post(
                url,
                headers=self._headers(),
                json=json_body if json_body is not None else {},
            )
        except httpx.HTTPError as exc:
            raise UploadError(f"{operation} network request failed.") from exc
        payload = self._json(response, operation)
        error = payload.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").casefold()
            message = str(error.get("message") or "")
            if code and code != "ok":
                detail = f"{message} ({code})"
                normalized = f"{detail} {code}".casefold()
                limit_markers = (
                    "rate_limit",
                    "rate limit",
                    "spam",
                    "too many",
                    "daily",
                    "quota",
                    "violation",
                )
                if any(marker in normalized for marker in limit_markers):
                    raise UploadLimitError("TikTok", detail)
                raise UploadError(f"{operation} failed: {detail}")
        if response.status_code >= 400:
            raise UploadError(f"{operation} failed: HTTP {response.status_code}")
        return payload

    @staticmethod
    def _json(response: httpx.Response, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise UploadError(f"{operation} returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise UploadError(f"{operation} returned invalid data.")
        return payload
