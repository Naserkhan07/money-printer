from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

from .errors import DownloadError
from .models import SourceVideo

logger = logging.getLogger(__name__)

_URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
}


def is_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").lower() in _ALLOWED_HOSTS


def extract_youtube_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in _URL_PATTERN.findall(text):
        candidate = match.rstrip(".,;:!?)]}'\"")
        if is_youtube_url(candidate) and candidate not in urls:
            urls.append(candidate)
    return urls


class VideoDownloader:
    def __init__(self, **_: object) -> None:
        pass

    async def download(self, url: str, destination: Path) -> SourceVideo:
        if not is_youtube_url(url):
            raise DownloadError("Only individual youtube.com or youtu.be URLs are accepted.")
        return await asyncio.to_thread(self._download_sync, url, destination)

    @staticmethod
    def _retry_delay(
        attempt: int = 1,
        *,
        n: int | None = None,
        **_: object,
    ) -> int:
        """Use bounded backoff across old and current yt-dlp callback signatures."""
        retry_number = n if n is not None else attempt
        return min(2 ** max(0, retry_number - 1), 20)

    @classmethod
    def _download_options(
        cls,
        output_template: str,
    ) -> dict[str, object]:
        import os
        import shutil
        node_bin = shutil.which("node")
        js_runtimes: dict[str, object] = {}
        if node_bin:
            js_runtimes["node"] = {"path": node_bin}
        js_runtimes["deno"] = {}

        # Log PO Token provider status
        try:
            import yt_dlp_plugins.extractor.getpot_bgutil_http  # noqa: F401
            logger.info("[downloader] yt-dlp PO Token provider plugin (bgutil:http) is active on 127.0.0.1:4416")
        except ImportError:
            pass

        yt_args: dict[str, object] = {
            "player_client": ["android", "ios", "mweb", "web"],
        }
        po_token = os.getenv("YOUTUBE_PO_TOKEN") or os.getenv("PO_TOKEN")
        visitor_data = os.getenv("YOUTUBE_VISITOR_DATA") or os.getenv("VISITOR_DATA")
        if po_token:
            logger.info("[downloader] Using configured PO Token from environment")
            yt_args["po_token"] = [po_token]
        if visitor_data:
            logger.info("[downloader] Using configured Visitor Data from environment")
            yt_args["visitor_data"] = [visitor_data]

        options: dict[str, object] = {
            # Prefer 1080p max, fall back to progressive stream (e.g. 18) when adaptive streams are restricted
            "format": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best/18/best",
            "outtmpl": output_template,
            "merge_output_format": "mkv",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "overwrites": True,
            "writeinfojson": True,
            # Windows networks and some ISPs intermittently reset YouTube CDN streams.
            # Resume partial files, force IPv4, use small HTTP chunks, and back off.
            "continuedl": True,
            "source_address": "0.0.0.0",
            "socket_timeout": 30,
            "http_chunk_size": 10 * 1024 * 1024,
            "concurrent_fragment_downloads": 8,
            # Mobile app client spoofing + PO Token plugin support (100% cookie-free)
            "extractor_args": {
                "youtube": yt_args,
            },
            # Current YouTube extraction requires a JS runtime for player challenges.
            "js_runtimes": js_runtimes,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
            "file_access_retries": 5,
            "retry_sleep_functions": {
                "http": cls._retry_delay,
                "fragment": cls._retry_delay,
                "extractor": cls._retry_delay,
            },
        }
        return options

    @staticmethod
    def _extract_info(url: str, options: dict[str, object]) -> dict[str, object]:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(url, download=True)
        if not isinstance(info, dict):
            raise DownloadError("YouTube returned no video metadata.")
        return info

    def _download_error_detail(self, detail: str) -> str:
        normalized = detail.casefold()
        if "sign in to confirm" in normalized or "bot" in normalized:
            return "YouTube botguard challenge encountered; URL will retry with mobile client extraction."
        if "requested format is not available" in normalized:
            return "YouTube did not expose compatible video streams for this URL."
        return detail

    @staticmethod
    def _probe_duration_safe(path: Path) -> float:
        import subprocess
        try:
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True, text=True, check=True,
            )
            return float(probe.stdout.strip())
        except Exception:
            return 60.0

    def _download_pytubefix(self, url: str, destination: Path) -> SourceVideo:
        import os
        from pytubefix import YouTube

        destination.mkdir(parents=True, exist_ok=True)
        po_token = os.getenv("YOUTUBE_PO_TOKEN") or os.getenv("PO_TOKEN")
        visitor_data = os.getenv("YOUTUBE_VISITOR_DATA") or os.getenv("VISITOR_DATA")

        kwargs: dict[str, object] = {}
        if po_token and visitor_data:
            kwargs["use_po_token"] = True
            kwargs["po_token_verifier"] = lambda: (visitor_data, po_token)

        # Prioritize clients that bypass botguard challenges (zero cookies)
        for client in ("VISION_OS", "ANDROID", "TV", "MWEB"):
            try:
                yt = YouTube(url, client=client, **kwargs)
                title = yt.title or "Untitled video"
                duration = float(yt.length or 0)
                uploader = yt.author or "Unknown creator"
                vid_id = yt.video_id or "unknown"

                prog = (
                    yt.streams.filter(progressive=True, file_extension="mp4")
                    .order_by("resolution")
                    .desc()
                    .first()
                    or yt.streams.filter(progressive=True).order_by("resolution").desc().first()
                )
                if prog:
                    out_path = destination / f"source.{prog.subtype}"
                    prog.download(output_path=str(destination), filename=out_path.name)
                    if duration <= 0:
                        duration = self._probe_duration_safe(out_path)
                    return SourceVideo(
                        path=out_path,
                        source_url=url,
                        video_id=vid_id,
                        title=title,
                        uploader=uploader,
                        duration_seconds=duration,
                    )
            except Exception as exc:
                logger.warning("[pytubefix] client %s failed for %s: %s", client, url, exc)
                continue

        raise DownloadError("YouTube download failed with both yt-dlp and pytubefix.")

    def _download_sync(self, url: str, destination: Path) -> SourceVideo:
        destination.mkdir(parents=True, exist_ok=True)
        output_template = str(destination / "source.%(ext)s")

        # 1. Primary attempt: pure mobile client (100% cookie-free)
        options = self._download_options(output_template)
        info: dict[str, object] | None = None
        try:
            info = self._extract_info(url, options)
        except yt_dlp.utils.DownloadError as exc:
            detail = str(exc)
            if "live event will begin" in detail.casefold() or "premieres in" in detail.casefold():
                raise DownloadError(f"Scheduled livestream/premiere cannot be downloaded: {detail}")
            elif "video is unavailable" in detail.casefold() or "this video is unavailable" in detail.casefold() or "private video" in detail.casefold():
                raise DownloadError(f"Video is unavailable or private on YouTube: {detail}")

            # 2. Fallback: pytubefix Android progressive stream
            try:
                return self._download_pytubefix(url, destination)
            except Exception:
                raise DownloadError(
                    f"YouTube download failed: {self._download_error_detail(detail)}"
                ) from exc
        except Exception as exc:
            raise DownloadError(f"Unexpected downloader error: {exc}") from exc

        candidates = [
            path
            for path in destination.glob("source.*")
            if path.is_file() and path.suffix not in {".json", ".part", ".ytdl"}
        ]
        if not candidates:
            raise DownloadError("The downloader finished but no video file was created.")
        source_path = max(candidates, key=lambda path: path.stat().st_size)
        duration = float(info.get("duration") or 0)
        if duration <= 0:
            raise DownloadError("Could not determine the source video's duration.")
        if duration < 20:
            raise DownloadError("The source video is shorter than the minimum 20-second Short.")

        return SourceVideo(
            path=source_path,
            source_url=str(info.get("webpage_url") or url),
            video_id=str(info.get("id") or "unknown"),
            title=str(info.get("title") or "Untitled video"),
            uploader=str(info.get("uploader") or info.get("channel") or "Unknown creator"),
            duration_seconds=duration,
        )
