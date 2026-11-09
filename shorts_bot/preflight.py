from __future__ import annotations

import asyncio
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .db import JobRepository
from .pipeline import WorkflowServices

logger = logging.getLogger(__name__)


async def get_system_health(settings: Settings | None = None) -> dict[str, Any]:
    """
    Performs comprehensive preflight checks across all configured platforms,
    APIs, tokens, and local media tools.
    """
    if settings is None:
        settings = Settings.from_env()

    results: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "tools": {},
        "services": {},
        "overall_status": "healthy",
    }

    # 1. Local Media Tools
    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    results["tools"]["ffmpeg"] = {
        "status": "ready" if ffmpeg_bin else "missing",
        "path": ffmpeg_bin or "",
    }
    results["tools"]["ffprobe"] = {
        "status": "ready" if ffprobe_bin else "missing",
        "path": ffprobe_bin or "",
    }

    # 2. Services Verification
    try:
        repo = JobRepository(settings.database_path)
        services = WorkflowServices.from_settings(settings, repository=repo)

        # Groq
        try:
            model, transcription_model = await services.planner.check_models()
            results["services"]["groq"] = {
                "status": "ready",
                "details": f"Model: {model} | Transcription: {transcription_model}",
            }
        except Exception as exc:
            results["services"]["groq"] = {"status": "error", "error": str(exc)}
            results["overall_status"] = "degraded"

        # YouTube
        if services.youtube_uploader:
            try:
                channel = await services.youtube_uploader.check_connection()
                results["services"]["youtube"] = {
                    "status": "ready",
                    "channel": channel,
                }
            except Exception as exc:
                results["services"]["youtube"] = {"status": "error", "error": str(exc)}
                results["overall_status"] = "degraded"
        else:
            results["services"]["youtube"] = {"status": "disabled"}

        # Instagram
        if services.instagram_uploader:
            try:
                username = await services.instagram_uploader.check_connection()
                results["services"]["instagram"] = {
                    "status": "ready",
                    "account": f"@{username}",
                }
            except Exception as exc:
                results["services"]["instagram"] = {"status": "error", "error": str(exc)}
                results["overall_status"] = "degraded"
        else:
            results["services"]["instagram"] = {"status": "disabled"}

        # Facebook
        if services.facebook_uploader:
            try:
                page_name = await services.facebook_uploader.check_connection()
                results["services"]["facebook"] = {
                    "status": "ready",
                    "page": page_name,
                }
            except Exception as exc:
                results["services"]["facebook"] = {"status": "error", "error": str(exc)}
                results["overall_status"] = "degraded"
        else:
            results["services"]["facebook"] = {"status": "disabled"}

        # Cloudinary
        if settings.cloudinary_cloud_name and settings.cloudinary_api_key:
            results["services"]["cloudinary"] = {
                "status": "configured",
                "cloud_name": settings.cloudinary_cloud_name,
            }
        else:
            results["services"]["cloudinary"] = {"status": "not_configured"}

    except Exception as exc:
        results["overall_status"] = "error"
        results["error"] = str(exc)

    return results


def run_preflight_cli() -> None:
    """CLI runner for health check."""
    print("=" * 60)
    print("🚀 AUTO-POSTER System & Token Health Preflight")
    print("=" * 60)
    health = asyncio.run(get_system_health())
    print(f"Overall Status: {health.get('overall_status').upper()}\n")

    print("[TOOLS]")
    for tool, info in health.get("tools", {}).items():
        status = info.get("status")
        icon = "✅" if status == "ready" else "❌"
        print(f"  {icon} {tool}: {status} ({info.get('path', 'not found')})")

    print("\n[SERVICES & PLATFORMS]")
    for svc, info in health.get("services", {}).items():
        status = info.get("status")
        if status == "ready":
            icon = "✅"
            extra = info.get("channel") or info.get("account") or info.get("page") or info.get("details") or ""
            print(f"  {icon} {svc.capitalize()}: READY ({extra})")
        elif status == "configured":
            print(f"  ✅ {svc.capitalize()}: CONFIGURED ({info.get('cloud_name')})")
        elif status == "disabled":
            print(f"  ⚪ {svc.capitalize()}: DISABLED")
        else:
            print(f"  ❌ {svc.capitalize()}: ERROR - {info.get('error')}")
    print("=" * 60)


if __name__ == "__main__":
    run_preflight_cli()

