"""Safe, conflict-free synchronization of links.txt and downloaded-links.log with origin/main."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from shorts_bot.config import Settings
from shorts_bot.downloader import is_youtube_url

logger = logging.getLogger("shorts_bot.sync_ledger")


def run_cmd(
    args: Sequence[str],
    check: bool = True,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def extract_url(line: str) -> str:
    """Extract and normalize the YouTube URL from a tab-delimited ledger log line."""
    parts = line.strip().split("\t")
    if len(parts) >= 3:
        return parts[2].strip().rstrip("/")
    # Fallback: check if any part contains youtube.com or youtu.be
    for part in parts:
        candidate = part.strip()
        if "youtube.com" in candidate or "youtu.be" in candidate:
            return candidate.rstrip("/")
    return ""


def merge_ledger_and_queue(
    local_log_lines: list[str],
    local_links_lines: list[str],
    remote_log_lines: list[str],
    remote_links_lines: list[str],
) -> tuple[list[str], list[str]]:
    """
    Merge local and remote lines without line-conflict rebasing:
    1. Remote log lines are kept intact.
    2. Any local log line whose URL or job_id is not already in remote is appended.
    3. All downloaded URLs are gathered.
    4. Remote links.txt lines are filtered to remove any downloaded URLs (and their Autopilot header).
    5. Any local manual links that were queued locally but aren't in remote (and not downloaded) are appended.
    """
    # 1 & 2. Merge downloaded ledger lines
    remote_urls = {extract_url(line) for line in remote_log_lines if extract_url(line)}
    remote_jobs = {
        line.split("\t")[1].strip()
        for line in remote_log_lines
        if len(line.split("\t")) >= 2 and line.split("\t")[1].strip()
    }

    merged_log = list(remote_log_lines)
    for line in local_log_lines:
        line_clean = line.strip()
        if not line_clean:
            continue
        url = extract_url(line_clean)
        parts = line_clean.split("\t")
        job_id = parts[1].strip() if len(parts) >= 2 else ""

        is_new = (url and url not in remote_urls) or (job_id and job_id not in remote_jobs)
        if is_new and line_clean not in merged_log:
            merged_log.append(line_clean)
            if url:
                remote_urls.add(url)
            if job_id:
                remote_jobs.add(job_id)

    # 3. All downloaded URLs
    all_downloaded = {extract_url(line) for line in merged_log if extract_url(line)}

    # 4. Filter base links from remote
    cleaned_links: list[str] = []
    seen_urls: set[str] = set()

    for line in remote_links_lines:
        candidate = line.strip()
        if candidate and not candidate.startswith("#"):
            url_norm = candidate.rstrip("/")
            if url_norm in all_downloaded or url_norm in seen_urls:
                # Dropped because it was downloaded or duplicate
                if cleaned_links and cleaned_links[-1].strip().startswith("# [Autopilot:"):
                    cleaned_links.pop()
                continue
            seen_urls.add(url_norm)
        cleaned_links.append(line)

    # 5. Check if local_links_lines has manual URLs not yet in remote_links_lines
    for line in local_links_lines:
        candidate = line.strip()
        if candidate and not candidate.startswith("#") and is_youtube_url(candidate):
            url_norm = candidate.rstrip("/")
            if url_norm not in all_downloaded and url_norm not in seen_urls:
                cleaned_links.append(candidate)
                seen_urls.add(url_norm)

    return merged_log, cleaned_links


def sync_and_commit(settings: Settings | None = None) -> bool:
    """Sync ledger and links with origin/main and push."""
    settings = settings or Settings.from_env()
    links_path = settings.links_file
    log_path = settings.downloaded_links_log

    # Read current local state
    local_log_lines: list[str] = []
    if log_path.exists():
        local_log_lines = [
            line.strip()
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    local_links_lines: list[str] = []
    if links_path.exists():
        local_links_lines = links_path.read_text(encoding="utf-8").splitlines()

    # Configure git author if not set
    try:
        run_cmd(["git", "config", "user.name"], check=True)
    except subprocess.CalledProcessError:
        run_cmd(["git", "config", "--local", "user.name", "github-actions[bot]"], check=False)
        run_cmd(["git", "config", "--local", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)

    # Abort any lingering interrupted rebase or merge
    run_cmd(["git", "rebase", "--abort"], check=False)
    run_cmd(["git", "merge", "--abort"], check=False)

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[sync_ledger] Fetching origin/main (attempt {attempt}/{max_attempts})...")
            fetch_res = run_cmd(["git", "fetch", "origin", "main"], check=False)
            if fetch_res.returncode != 0:
                print(f"[sync_ledger] Warning: git fetch failed: {fetch_res.stderr.strip()}", file=sys.stderr)

            # Get remote versions directly via git show
            remote_log_proc = run_cmd(["git", "show", "origin/main:work/downloaded-links.log"], check=False)
            remote_log_lines = (
                [l.strip() for l in remote_log_proc.stdout.splitlines() if l.strip()]
                if remote_log_proc.returncode == 0
                else local_log_lines
            )

            remote_links_proc = run_cmd(["git", "show", "origin/main:links.txt"], check=False)
            remote_links_lines = (
                remote_links_proc.stdout.splitlines()
                if remote_links_proc.returncode == 0
                else local_links_lines
            )

            merged_log, merged_links = merge_ledger_and_queue(
                local_log_lines,
                local_links_lines,
                remote_log_lines,
                remote_links_lines,
            )

            # Checkout clean copies from origin/main
            run_cmd(["git", "checkout", "origin/main", "--", str(links_path), str(log_path)], check=False)

            # Write merged content
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("\n".join(merged_log) + "\n", encoding="utf-8")
            links_path.write_text("\n".join(merged_links) + "\n", encoding="utf-8")

            # Check if any change exists against index/HEAD
            run_cmd(["git", "add", "-f", str(links_path), str(log_path)], check=True)
            diff_proc = run_cmd(["git", "diff", "--staged", "--quiet"], check=False)
            if diff_proc.returncode == 0:
                print("[sync_ledger] No queue or ledger changes to commit.")
                return True

            # Commit changes
            run_cmd(
                ["git", "commit", "-m", "chore(autopilot): update links queue & download audit ledger [skip ci]"],
                check=True,
            )

            # Push directly to origin/main
            push_proc = run_cmd(["git", "push", "origin", "main"], check=False)
            if push_proc.returncode == 0:
                print("[sync_ledger] Successfully pushed ledger and queue updates to origin/main.")
                return True

            print(
                f"[sync_ledger] Push attempt {attempt} rejected ({push_proc.stderr.strip()}); "
                "re-syncing on top of fresh origin/main...",
                file=sys.stderr,
            )
            # Reset commit and retry with fresh fetch
            run_cmd(["git", "reset", "--hard", "origin/main"], check=False)

        except Exception as exc:
            print(f"[sync_ledger] Attempt {attempt} encountered error: {exc}", file=sys.stderr)
            run_cmd(["git", "rebase", "--abort"], check=False)
            run_cmd(["git", "merge", "--abort"], check=False)

    print(
        "[sync_ledger] Notice: Could not push updated ledger to origin/main after retries. "
        "Work artifacts and uploads are safe.",
        file=sys.stderr,
    )
    return False


if __name__ == "__main__":
    success = sync_and_commit()
    # Always exit 0 so non-critical git ledger sync doesn't fail the entire workflow run
    sys.exit(0)
