"""Published-performance insights for YouTube uploads.

Concept adapted from the insights/analytics work in Social-Scheduler by
Liparoto (dashboard insights hub) and the history pages in notherobot's
PostFlow — reimagined as a small Python module that caches the YouTube Data
API v3 video statistics into the local SQLite database so the dashboard and
CLI can report views/likes/comments without hitting the API every time.

Reads require only the ``youtube.readonly`` scope, which is already part of
the OAuth grant created by ``shorts-auth`` — no re-authorization needed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .config import Settings
from .db import JobRepository
from .errors import ConfigurationError, UploadError
from .retry import with_retry
from .youtube import YOUTUBE_READONLY_SCOPE

logger = logging.getLogger(__name__)

_BATCH_SIZE = 50  # YouTube videos.list accepts at most 50 IDs per call


def _default_service_factory(token_file: Path) -> Any:
    if not token_file.exists():
        raise ConfigurationError(
            f"YouTube OAuth token not found at {token_file}. Run shorts-auth first."
        )
    try:
        credentials = Credentials.from_authorized_user_file(
            str(token_file), [YOUTUBE_READONLY_SCOPE]
        )
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            token_file.write_text(credentials.to_json(), encoding="utf-8")
        if not credentials.valid:
            raise ConfigurationError(
                f"YouTube OAuth token at {token_file} is invalid. Run shorts-auth again."
            )
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError(f"Could not load YouTube OAuth token: {exc}") from exc
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


@with_retry(max_attempts=3, delay_seconds=5)
def _list_video_statistics(service: Any, video_ids: list[str]) -> list[dict[str, Any]]:
    response = service.videos().list(part="statistics,snippet", id=",".join(video_ids)).execute()
    items = response.get("items", []) if isinstance(response, dict) else []
    results: list[dict[str, Any]] = []
    for item in items:
        statistics = item.get("statistics", {}) if isinstance(item.get("statistics"), dict) else {}
        snippet = item.get("snippet", {}) if isinstance(item.get("snippet"), dict) else {}

        def _int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        results.append(
            {
                "video_id": str(item.get("id") or ""),
                "title": str(snippet.get("title") or ""),
                "views": _int(statistics.get("viewCount")),
                "likes": _int(statistics.get("likeCount")),
                "comments": _int(statistics.get("commentCount")),
            }
        )
    return results


def sync_youtube_insights(
    repository: JobRepository,
    token_file: Path,
    *,
    service_factory: Callable[[Path], Any] | None = None,
) -> dict[str, int]:
    """Fetch statistics for every recorded YouTube upload into ``video_stats``."""
    video_ids = repository.list_youtube_video_ids()
    summary = {"requested": len(video_ids), "found": 0, "written": 0, "missing": 0}
    if not video_ids:
        return summary

    factory = service_factory or _default_service_factory
    service = factory(token_file)

    fetched: list[dict[str, Any]] = []
    for start in range(0, len(video_ids), _BATCH_SIZE):
        batch = video_ids[start : start + _BATCH_SIZE]
        try:
            fetched.extend(_list_video_statistics(service, batch))
        except Exception as exc:
            raise UploadError(f"Could not fetch YouTube statistics: {exc}") from exc

    found_ids = {row["video_id"] for row in fetched}
    summary["found"] = len(found_ids)
    summary["missing"] = len(video_ids) - len(found_ids)
    summary["written"] = repository.upsert_video_stats(fetched)
    return summary


def render_report(stats: list[dict[str, object]], limit: int | None = None) -> str:
    rows = stats if limit is None else stats[:limit]
    if not rows:
        return "No cached YouTube statistics yet. Run: shorts-insights (or shorts-insights sync)."
    header = f"{'Views':>10}  {'Likes':>8}  {'Comments':>8}  Title"
    lines = [header, "-" * len(header)]
    total_views = 0
    for row in rows:
        views = int(row.get("views") or 0)
        likes = int(row.get("likes") or 0)
        comments = int(row.get("comments") or 0)
        total_views += views
        title = str(row.get("title") or row.get("video_id") or "")[:60]
        lines.append(f"{views:>10}  {likes:>8}  {comments:>8}  {title}")
    lines.append("-" * len(header))
    lines.append(f"{total_views:>10}  {'':>8}  {'':>8}  TOTAL ({len(rows)} videos)")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync YouTube statistics for uploaded Shorts into the local database."
    )
    parser.add_argument("--limit", type=int, default=25, help="Rows to print (default: 25)")
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Fetch and cache statistics without printing the report",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    settings = Settings.from_env()
    repository = JobRepository(settings.database_path)

    try:
        summary = sync_youtube_insights(repository, settings.youtube_token_file)
    except (ConfigurationError, UploadError) as exc:
        print(f"Insights sync failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(
        f"Synced {summary['found']}/{summary['requested']} videos "
        f"({summary['written']} cached, {summary['missing']} no longer available)."
    )
    if not args.sync_only:
        print()
        print(render_report(repository.list_video_stats(limit=args.limit)))


if __name__ == "__main__":
    main()
