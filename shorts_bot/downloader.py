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
_cached_downloader_po: tuple[float, str | None] = (0.0, None)


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
    def _resolve_po_token(cls) -> str | None:
        global _cached_downloader_po
        import os
        import time

        import httpx

        token = os.getenv("YOUTUBE_PO_TOKEN") or os.getenv("PO_TOKEN")
        if token:
            return token

        now = time.time()
        last_check, cached_val = _cached_downloader_po
        if now - last_check < 300:
            return cached_val

        provider_url = os.getenv("PO_TOKEN_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
        resolved: str | None = None
        for path in ("/api/po_token", "/po_token", "/api/v1/po_token"):
            try:
                with httpx.Client(timeout=1.5) as client:
                    resp = client.get(f"{provider_url}{path}")
                if resp.status_code != 200:
                    continue
                try:
                    data = resp.json()
                except Exception:
                    data = None
                if isinstance(data, dict):
                    for key in ("po_token", "token", "access_token"):
                        val = data.get(key)
                        if isinstance(val, str) and val:
                            resolved = val
                            break
                if not resolved:
                    text = resp.text.strip()
                    if text and len(text) > 20 and " " not in text:
                        resolved = text
                if resolved:
                    break
            except (httpx.ConnectError, httpx.ConnectTimeout):
                break
            except Exception:
                continue

        _cached_downloader_po = (now, resolved)
        return resolved

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

        cookies = os.getenv("YTDLP_COOKIES") or os.getenv("YOUTUBE_COOKIES")
        cookie_file: str | None = None
        if cookies:
            cookie_path = Path("work") / "youtube_cookies.txt"
            cookie_path.parent.mkdir(parents=True, exist_ok=True)
            cookie_path.write_text(cookies, encoding="utf-8")
            cookie_file = str(cookie_path)
            logger.info("[downloader] Using YouTube cookies from YTDLP_COOKIES env: %s", cookie_path)
        else:
            repo_cookies = Path("youtube_cookies.txt")
            if repo_cookies.exists():
                cookie_file = str(repo_cookies)
                logger.info("[downloader] Using YouTube cookies file: %s", repo_cookies)

        yt_args: dict[str, object] = {}
        # Client selection rule:
        # If desktop browser cookies are provided, MUST use web/mweb clients.
        # Sending desktop cookies with android/ios client signatures triggers HTTP 403 Forbidden on YouTube CDN.
        if cookie_file:
            yt_args["player_client"] = ["web", "mweb"]
        else:
            yt_args["player_client"] = ["mweb", "web", "ios"]

        po_token = cls._resolve_po_token()
        env_source = "env" if (os.getenv("YOUTUBE_PO_TOKEN") or os.getenv("PO_TOKEN")) else "provider"
        visitor_data = os.getenv("YOUTUBE_VISITOR_DATA") or os.getenv("VISITOR_DATA")
        if po_token:
            logger.info("[downloader] Using PO Token from %s (cookie-free extraction)", env_source)
            yt_args["po_token"] = [po_token]
        if visitor_data:
            logger.info("[downloader] Using configured Visitor Data from environment")
            yt_args["visitor_data"] = [visitor_data]

        extractor_args: dict[str, dict[str, object]] = {
            "youtube": yt_args,
        }
        pot_provider_url = os.getenv("PO_TOKEN_PROVIDER_URL", "http://127.0.0.1:4416").rstrip("/")
        if pot_provider_url:
            extractor_args["youtubepot-bgutilhttp"] = {
                "base_url": [pot_provider_url],
            }

        options: dict[str, object] = {
            # Prefer 1080p max, fall back to progressive stream (e.g. 18/22) when adaptive streams are restricted
            "format": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best/18/best",
            "outtmpl": output_template,
            "merge_output_format": "mkv",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "restrictfilenames": True,
            "overwrites": True,
            "writeinfojson": True,
            "continuedl": True,
            "source_address": "0.0.0.0",
            "socket_timeout": 30,
            # Single fragment download and max 25MB/s rate to avoid triggering YouTube CDN's 403 Forbidden anti-flood block
            "concurrent_fragment_downloads": 1,
            "ratelimit": 25 * 1024 * 1024,
            "extractor_args": extractor_args,
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

        if cookie_file:
            options["cookiefile"] = cookie_file

        proxy = (
            os.getenv("YOUTUBE_PROXY")
            or os.getenv("PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("HTTPS_PROXY")
        )
        if proxy:
            masked = proxy.split("@")[-1] if "@" in proxy else proxy[:20]
            logger.info("[downloader] Routing YouTube downloads through proxy: %s", masked)
            options["proxy"] = proxy

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
        po_token = self._resolve_po_token()
        visitor_data = os.getenv("YOUTUBE_VISITOR_DATA") or os.getenv("VISITOR_DATA")
        proxy = (
            os.getenv("YOUTUBE_PROXY")
            or os.getenv("PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("HTTPS_PROXY")
        )

        kwargs: dict[str, object] = {}
        if po_token:
            kwargs["use_po_token"] = True
            kwargs["po_token_verifier"] = lambda: (visitor_data or "", po_token)
            logger.info("[pytubefix] Using PO Token for botguard bypass")
        if proxy:
            kwargs["proxies"] = {"http": proxy, "https": proxy}

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

    def _download_cobalt(self, url: str, destination: Path) -> SourceVideo:
        """Fallback to Cobalt API (imputnet/cobalt) stream extraction."""
        import json
        import os
        import httpx

        destination.mkdir(parents=True, exist_ok=True)
        custom_endpoint = os.getenv("COBALT_API_URL", "").strip()
        api_key = os.getenv("COBALT_API_KEY", "").strip()

        endpoints: list[str] = []
        if custom_endpoint:
            endpoints.append(custom_endpoint)
        endpoints.extend([
            "https://cobalt-api.kwiatekm.tokyo",
            "https://cobalt.canine.tools",
            "https://cobalt.dimden.dev",
            "https://cobalt.moe",
            "https://api.cobalt.tools",
        ])

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        if api_key:
            headers["Authorization"] = f"Api-Key {api_key}"

        payload = {
            "url": url,
            "videoQuality": "1080",
            "downloadMode": "auto",
        }

        vid_match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url) or re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
        vid_id = vid_match.group(1) if vid_match else "unknown"

        proxy = (
            os.getenv("YOUTUBE_PROXY")
            or os.getenv("PROXY")
            or os.getenv("HTTP_PROXY")
            or os.getenv("HTTPS_PROXY")
        )

        for ep in endpoints:
            base_ep = ep.rstrip("/")
            post_url = f"{base_ep}/" if not base_ep.endswith("/api") else base_ep
            try:
                logger.info("[Cobalt] Querying endpoint %s for video %s", base_ep, vid_id)
                client_kwargs: dict[str, object] = {
                    "timeout": 25.0,
                    "follow_redirects": True,
                }
                if proxy:
                    client_kwargs["proxy"] = proxy

                with httpx.Client(**client_kwargs) as client:
                    resp = client.post(post_url, headers=headers, json=payload)
                    if resp.status_code not in (200, 201):
                        logger.warning("[Cobalt] Endpoint %s returned %s: %s", base_ep, resp.status_code, resp.text[:100])
                        continue
                    data = resp.json()
                    status = data.get("status")
                    stream_url = data.get("url")
                    if status in ("tunnel", "redirect") and stream_url:
                        out_path = destination / "source.mp4"
                        logger.info("[Cobalt] Downloading media stream from %s...", base_ep)
                        with client.stream("GET", stream_url, timeout=180.0) as stream_resp:
                            stream_resp.raise_for_status()
                            with open(out_path, "wb") as f:
                                for chunk in stream_resp.iter_bytes(chunk_size=65536):
                                    f.write(chunk)

                        if out_path.exists() and out_path.stat().st_size > 1024:
                            dur = self._probe_duration_safe(out_path)
                            title = data.get("filename", f"YouTube Video {vid_id}").rsplit(".", 1)[0]
                            logger.info("[Cobalt] Successfully downloaded %s (%.1fs)", out_path.name, dur)
                            return SourceVideo(
                                path=out_path,
                                source_url=url,
                                video_id=vid_id,
                                title=title,
                                uploader="YouTube Creator",
                                duration_seconds=dur,
                            )
            except Exception as exc:
                logger.debug("[Cobalt] Endpoint %s error: %s", base_ep, exc)
                continue

        raise DownloadError("Cobalt API fallback failed to retrieve media stream.")

    def _download_sync(self, url: str, destination: Path) -> SourceVideo:
        destination.mkdir(parents=True, exist_ok=True)
        output_template = str(destination / "source.%(ext)s")

        # 1. Primary attempt: yt-dlp adaptive stream (1080p)
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

            # 2. Progressive single-stream fallback (e.g. 720p/360p pre-merged MP4)
            # Progressive streams avoid DASH fragmentation and SABR chunking that trigger CDN 403 Forbidden.
            logger.warning("[downloader] Adaptive yt-dlp failed (%s); trying progressive stream fallback...", detail[:120])
            try:
                prog_options = dict(options)
                prog_options["format"] = "22/18/best[height<=720][ext=mp4]/best[ext=mp4]/best"
                info = self._extract_info(url, prog_options)
            except Exception as prog_exc:
                logger.warning("[downloader] Progressive yt-dlp failed (%s); trying iOS/mweb client fallback...", prog_exc)
                # 3. Alternative client fallback: iOS / mweb
                try:
                    ios_options = dict(options)
                    ios_options["format"] = "22/18/best[height<=720]/best"
                    ext_args = dict(ios_options.get("extractor_args", {}))
                    ext_args["youtube"] = {"player_client": ["ios", "mweb"]}
                    ios_options["extractor_args"] = ext_args
                    info = self._extract_info(url, ios_options)
                except Exception as ios_exc:
                    logger.warning("[downloader] Alternative client yt-dlp failed (%s); trying pytubefix fallback...", ios_exc)
                    # 4. Fallback: pytubefix Android/VisionOS progressive stream
                    try:
                        return self._download_pytubefix(url, destination)
                    except Exception as pyfix_exc:
                        logger.warning("[downloader] pytubefix failed (%s); trying Cobalt API fallback...", pyfix_exc)
                        # 5. Fallback: Cobalt API stream
                        try:
                            return self._download_cobalt(url, destination)
                        except Exception as cobalt_exc:
                            logger.warning("[downloader] Cobalt API fallback failed: %s", cobalt_exc)
                            raise DownloadError(
                                f"YouTube download failed: {self._download_error_detail(detail)}"
                            ) from exc
        except Exception as exc:
            try:
                return self._download_cobalt(url, destination)
            except Exception:
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
