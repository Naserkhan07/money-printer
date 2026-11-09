"""
Unified Cloud Entrypoint for Render.
Runs the Live Mission Control Web Dashboard on $PORT while supervising
the 24/7 autonomous video downloading, AI clipping, and multi-platform posting worker.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="[Render Supervisor] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("render_supervisor")


def _write_file_from_env(
    var_names: list[str],
    target_path: Path,
    is_base64_candidates: list[str] | None = None,
) -> bool:
    """Materializes a credential file from environment variables if not already on disk."""
    if target_path.is_file() and target_path.stat().st_size > 0:
        logger.info("Found existing credential file on disk: %s", target_path)
        return True

    # 1. Check raw content candidates
    for name in var_names:
        val = os.environ.get(name, "").strip()
        if val:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(val, encoding="utf-8")
            logger.info("Successfully materialized %s from $%s (%d bytes)", target_path, name, len(val))
            return True

    # 2. Check base64 candidates
    for name in (is_base64_candidates or []):
        b64_val = os.environ.get(name, "").strip()
        if b64_val:
            try:
                decoded = base64.b64decode(b64_val)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(decoded)
                logger.info("Successfully materialized %s from base64 $%s (%d bytes)", target_path, name, len(decoded))
                return True
            except Exception as exc:
                logger.error("Failed to decode base64 from $%s: %s", name, exc)

    return False


def setup_cloud_environment() -> None:
    """Prepares directories and materializes cloud secrets from Render environment variables."""
    load_dotenv()

    # Create standard runtime directories
    for folder in ["work", "queue", "assets/gameplay/gta5", "assets/gameplay/minecraft", "assets/gameplay/subway_surfers"]:
        Path(folder).mkdir(parents=True, exist_ok=True)

    # 1. YouTube OAuth Token
    token_str = (os.environ.get("YOUTUBE_TOKEN_FILE") or "").strip() or "youtube_token.json"
    token_path = Path(token_str)
    _write_file_from_env(
        var_names=["YOUTUBE_TOKEN_CONTENT", "YOUTUBE_TOKEN_JSON"],
        target_path=token_path,
        is_base64_candidates=["YOUTUBE_TOKEN_BASE64"],
    )

    # 2. YouTube Client Secrets
    secrets_str = (os.environ.get("YOUTUBE_CLIENT_SECRETS_FILE") or "").strip() or "client_secret.json"
    secrets_path = Path(secrets_str)
    _write_file_from_env(
        var_names=["CLIENT_SECRET_CONTENT", "YOUTUBE_CLIENT_SECRETS_CONTENT", "CLIENT_SECRET_JSON"],
        target_path=secrets_path,
        is_base64_candidates=["CLIENT_SECRET_BASE64", "YOUTUBE_CLIENT_SECRETS_BASE64"],
    )

    # 3. YouTube Cookies (if provided)
    cookies_str = (os.environ.get("YTDLP_COOKIE_FILE") or "").strip() or "youtube-cookies.txt"
    cookies_path = Path(cookies_str)
    materialized_cookies = _write_file_from_env(
        var_names=["YOUTUBE_COOKIES_CONTENT", "YOUTUBE_COOKIES_TXT", "COOKIES_CONTENT"],
        target_path=cookies_path,
        is_base64_candidates=["YOUTUBE_COOKIES_BASE64", "COOKIES_BASE64"],
    )
    if materialized_cookies:
        os.environ["YTDLP_COOKIE_FILE"] = str(cookies_path)


def start_web_dashboard(port: int) -> None:
    """Runs the live mission control dashboard in a background thread."""
    import threading
    from http.server import ThreadingHTTPServer
    from shorts_bot.dashboard import DashboardHandler

    def _serve() -> None:
        try:
            server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
            logger.info("🚀 Render Web Dashboard listening on 0.0.0.0:%d", port)
            server.serve_forever()
        except Exception as exc:
            logger.error("Web Dashboard server failed: %s", exc)

    t = threading.Thread(target=_serve, daemon=True, name="DashboardThread")
    t.start()


def start_keep_alive_pinger() -> None:
    """
    Prevents Render from sleeping on the Free tier by regularly pinging /healthz.
    Render automatically populates $RENDER_EXTERNAL_URL on web services.
    """
    import threading
    import urllib.request

    url = (
        os.environ.get("RENDER_EXTERNAL_URL")
        or os.environ.get("KEEP_ALIVE_URL")
        or os.environ.get("RENDER_URL")
        or ""
    ).strip()

    if not url:
        logger.info(
            "🛡️ Keep-Alive pinger: No RENDER_EXTERNAL_URL detected. "
            "Render will supply this automatically when deployed as a Web Service."
        )
        return

    target = f"{url.rstrip('/')}/healthz"
    logger.info(
        "🛡️ Keep-Alive pinger active! Pinging %s every 9 minutes to prevent Render from sleeping.",
        target,
    )

    def _ping_loop() -> None:
        time.sleep(60)
        while True:
            try:
                req = urllib.request.Request(
                    target,
                    headers={"User-Agent": "MoneyPrinter-KeepAlive/1.0"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    if resp.status == 200:
                        logger.info(
                            "🛡️ Keep-Alive ping successful (HTTP 200) -> Render sleep timer reset!"
                        )
            except Exception as exc:
                logger.debug("Keep-Alive ping notice: %s", exc)
            time.sleep(540)  # Ping every 9 minutes (Render sleeps at 15m)

    t = threading.Thread(target=_ping_loop, daemon=True, name="KeepAliveThread")
    t.start()


def main() -> None:
    setup_cloud_environment()

    port = int(os.environ.get("PORT", 10000))
    logger.info("Initializing Money Printer on Render (Port %d)...", port)

    # 1. Start the HTTP Dashboard so Render's health checks succeed immediately
    start_web_dashboard(port)

    # 2. Start built-in keep-alive pinger so Render Free Tier never sleeps
    start_keep_alive_pinger()

    # 2. Supervisor Loop for the Automation Worker
    worker_proc: subprocess.Popen[Any] | None = None
    shutting_down = False

    def handle_signal(sig: int, frame: Any) -> None:
        nonlocal shutting_down
        shutting_down = True
        logger.info("Received termination signal %s. Shutting down gracefully...", sig)
        if worker_proc and worker_proc.poll() is None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker_proc.kill()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Allow 2 seconds for HTTP dashboard to bind before launching worker
    time.sleep(2)

    logger.info("Launching continuous 24/7 background worker (main.py)...")
    while not shutting_down:
        try:
            worker_proc = subprocess.Popen(
                [sys.executable, "main.py"],
                stdout=sys.stdout,
                stderr=sys.stderr,
            )
            ret_code = worker_proc.wait()
            if shutting_down:
                break
            logger.warning(
                "Automation worker process exited with code %s. Restarting in 5 seconds...",
                ret_code,
            )
            time.sleep(5)
        except Exception as exc:
            if shutting_down:
                break
            logger.error("Worker process error: %s. Restarting in 5 seconds...", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()
