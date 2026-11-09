"""
Live Mission Control Web Dashboard for AUTO-POSTER.
Features:
- Real-time schedule & upload countdowns (IST / Asia/Kolkata)
- Health status for YouTube (Splitzzz), Instagram (@splitzz.isodope), Facebook (Splitzz), Groq AI, FFmpeg
- In-browser Drag & Drop Video Dropzone to queue/
- Quick YouTube URL Queueing
- Autopilot Creator Channel Monitor & One-Click Trigger
- Live Published Links (YouTube Shorts, Instagram Reels, Facebook Reels)
- Modern Dark-Themed Responsive Glassmorphism UI
"""

from __future__ import annotations

import asyncio
import cgi
import html
import json
import logging
import os
import shutil
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .autopilot import AutopilotService
from .config import Settings
from .db import JobRepository
from .downloader import is_youtube_url
from .preflight import get_system_health
from .scheduler import ContentScheduler

logger = logging.getLogger(__name__)


def format_12h_time(iso_or_dt: str) -> str:
    """Converts ISO or standard timestamp string to 12-hour AM/PM format."""
    if not iso_or_dt:
        return ""
    try:
        clean = iso_or_dt.replace("Z", "+00:00").split(".")[0]
        dt = datetime.fromisoformat(clean)
        return dt.strftime("%b %d, %I:%M %p")
    except Exception:
        return iso_or_dt


def get_dashboard_data(db_path: Path, settings: Settings) -> dict[str, Any]:
    scheduler = ContentScheduler(
        daily_limits={
            "YouTube": settings.scheduler_max_daily_youtube,
            "Instagram": settings.scheduler_max_daily_instagram,
            "Facebook": settings.scheduler_max_daily_facebook,
        },
        advance_prep_minutes=settings.scheduler_advance_prep_minutes,
        timezone_str=settings.scheduler_timezone,
    )
    now = scheduler.current_dt()

    yt_slots = scheduler.get_slots_for_day("YouTube", now)
    ig_slots = scheduler.get_slots_for_day("Instagram", now)
    fb_slots = scheduler.get_slots_for_day("Facebook", now)

    yt_next = scheduler.get_next_slot("YouTube", now)
    ig_next = scheduler.get_next_slot("Instagram", now)
    fb_next = scheduler.get_next_slot("Facebook", now)

    yt_prep_due, yt_prep_slot = scheduler.is_prep_due("YouTube", now)
    ig_prep_due, ig_prep_slot = scheduler.is_prep_due("Instagram", now)
    fb_prep_due, fb_prep_slot = scheduler.is_prep_due("Facebook", now)

    uploaded_clips: list[dict[str, Any]] = []
    monitored_seen: list[dict[str, Any]] = []

    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT c.job_id, c.clip_index, c.title, c.youtube_video_id,
                           c.instagram_url, c.facebook_url, j.source_title, j.created_at,
                           j.status
                    FROM job_clips c
                    JOIN jobs j ON j.id = c.job_id
                    WHERE (c.youtube_video_id IS NOT NULL AND c.youtube_video_id != '')
                       OR (c.instagram_url IS NOT NULL AND c.instagram_url != '')
                       OR (c.facebook_url IS NOT NULL AND c.facebook_url != '')
                    ORDER BY j.created_at DESC
                    LIMIT 25
                    """
                ).fetchall()
                for r in rows:
                    created_at_raw = str(r["created_at"])
                    uploaded_clips.append(
                        {
                            "job_id": r["job_id"],
                            "clip_index": r["clip_index"],
                            "title": r["title"] or r["source_title"] or "Short Clip",
                            "youtube_video_id": r["youtube_video_id"],
                            "instagram_url": r["instagram_url"],
                            "facebook_url": r["facebook_url"],
                            "created_at": created_at_raw,
                            "created_at_12h": format_12h_time(created_at_raw),
                        }
                    )

                seen_rows = conn.execute(
                    """
                    SELECT video_id, channel_id, title, published_at, seen_at, queued
                    FROM autopilot_seen
                    ORDER BY seen_at DESC
                    LIMIT 15
                    """
                ).fetchall()
                for sr in seen_rows:
                    monitored_seen.append(
                        {
                            "video_id": sr["video_id"],
                            "channel_id": sr["channel_id"],
                            "title": sr["title"],
                            "published_at": sr["published_at"],
                            "seen_at": sr["seen_at"],
                            "seen_at_12h": format_12h_time(str(sr["seen_at"])),
                            "queued": bool(sr["queued"]),
                        }
                    )
        except Exception:
            pass

    pending_links: list[str] = []
    if settings.links_file.exists():
        try:
            lines = settings.links_file.read_text(encoding="utf-8").splitlines()
            for line in lines:
                candidate = line.strip()
                if candidate and not candidate.startswith("#"):
                    pending_links.append(candidate)
        except Exception:
            pass

    # Read queue/ folder files
    spool_files: list[str] = []
    queue_dir = Path("queue")
    if queue_dir.exists():
        for f in queue_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                spool_files.append(f.name)

    # Read active job
    active_job = None
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                active_row = conn.execute(
                    """
                    SELECT id, status, progress_message, source_title, created_at, updated_at
                    FROM jobs
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """
                ).fetchone()
                if active_row:
                    status_str = str(active_row["status"]).lower()
                    pct_map = {
                        "queued": 15,
                        "downloading": 30,
                        "analyzing": 50,
                        "rendering": 75,
                        "uploading": 90,
                        "complete": 100,
                        "failed": 0,
                    }
                    pct = pct_map.get(status_str, 50)
                    active_job = {
                        "id": active_row["id"],
                        "status": status_str,
                        "progress_message": active_row["progress_message"] or f"Stage: {status_str.title()}",
                        "title": active_row["source_title"] or "Recent Video Content",
                        "percent": pct,
                        "updated_at_12h": format_12h_time(str(active_row["updated_at"])),
                    }
        except Exception:
            pass

    if not active_job:
        active_job = {
            "status": "standby",
            "progress_message": "Autopilot & Spooler active. Standing by for next scheduled upload window.",
            "title": "System Active",
            "percent": 100,
            "updated_at_12h": now.strftime("%I:%M %p"),
        }

    return {
        "timestamp": now.strftime("%I:%M:%S %p"),
        "date_12h": now.strftime("%b %d, %Y - %I:%M %p"),
        "today_name": now.strftime("%A"),
        "active_job": active_job,
        "channels": list(settings.autopilot_channels),
        "pending_links": pending_links,
        "spool_files": spool_files,
        "uploaded_clips": uploaded_clips,
        "monitored_seen": monitored_seen,
        "schedules": {
            "YouTube": {
                "next_slot": yt_next.target_time.strftime("%I:%M %p"),
                "prep_due": yt_prep_due,
                "slots_today": [s.target_time.strftime("%I:%M %p") for s in yt_slots],
            },
            "Instagram": {
                "next_slot": ig_next.target_time.strftime("%I:%M %p"),
                "prep_due": ig_prep_due,
                "slots_today": [s.target_time.strftime("%I:%M %p") for s in ig_slots],
            },
            "Facebook": {
                "next_slot": fb_next.target_time.strftime("%I:%M %p"),
                "prep_due": fb_prep_due,
                "slots_today": [s.target_time.strftime("%I:%M %p") for s in fb_slots],
            },
        },
    }


def render_markdown_dashboard(data: dict[str, Any]) -> str:
    yt = data["schedules"]["YouTube"]
    ig = data["schedules"]["Instagram"]
    fb = data["schedules"]["Facebook"]
    job = data.get("active_job", {})
    pct = job.get("percent", 100)
    msg = job.get("progress_message", "Standby")
    title = job.get("title", "Ready")

    md: list[str] = []
    md.append("# 🚀 AUTO-POSTER Live Mission Control")
    md.append(f"**Live Server Time:** `{data['date_12h']}` | **Status:** ✅ `Running 24/7`")
    md.append("> 🌐 **Localhost Dashboard:** `http://localhost:5050`\n")

    bar_fill = min(20, max(0, pct // 5))
    bar_empty = 20 - bar_fill
    md.append(f"### ⚡ LIVE BOT PIPELINE & CUTTING PROGRESS: {pct}%")
    md.append(f"`[{'█' * bar_fill}{'░' * bar_empty}] {pct}% Complete`\n")
    md.append(f"- 🎬 **Current Action:** `{msg}`")
    md.append(f"- 📹 **Target Content:** `{title}`\n")
    s1 = "✅ Done" if pct >= 30 else ("⚡ In Progress" if pct > 10 else "⚪ Standby")
    s2 = "✅ Done" if pct >= 50 else ("⚡ Transcribing" if pct >= 30 else "⚪ Standby")
    s3 = "✅ Done" if pct >= 75 else ("⚡ Cutting Gameplay" if pct >= 50 else "⚪ Standby")
    s4 = "✅ Done" if pct >= 90 else ("⚡ Rendering Video" if pct >= 75 else "⚪ Standby")
    s5 = "✅ Uploaded" if pct >= 100 else ("⚡ Uploading Now" if pct >= 90 else "⚪ Standby")
    md.append("| 1. Download Video | 2. AI Whisper Subtitles | 3. GTA 5 / MC Cut | 4. Split Reel Render | 5. Post to Meta & YT |")
    md.append("| :---: | :---: | :---: | :---: | :---: |")
    md.append(f"| {s1} | {s2} | {s3} | {s4} | {s5} |\n")

    md.append("### 📅 Today's 12-Hour Upload Schedule")
    md.append("| Platform | Next Upload Window | Auto-Prep Status | Today's Slots |")
    md.append("| :--- | :--- | :--- | :--- |")
    md.append(f"| **YouTube Shorts** | **{yt['next_slot']}** | {'⚡ Auto-Prep Active' if yt['prep_due'] else 'Standby'} | {', '.join(yt['slots_today'])} |")
    md.append(f"| **Instagram Reels** | **{ig['next_slot']}** | {'⚡ Auto-Prep Active' if ig['prep_due'] else 'Standby'} | {', '.join(ig['slots_today'])} |")
    md.append(f"| **Facebook Reels** | **{fb['next_slot']}** | {'⚡ Auto-Prep Active' if fb['prep_due'] else 'Standby'} | {', '.join(fb['slots_today'])} |")
    md.append("")

    md.append("### 🎬 Recently Published Videos & Live Links")
    if data["uploaded_clips"]:
        md.append("| Video Title | Published | Direct Platform Links |")
        md.append("| :--- | :--- | :--- |")
        for c in data["uploaded_clips"][:10]:
            links: list[str] = []
            if c.get("youtube_video_id"):
                links.append(f"[🔴 YouTube Short](https://www.youtube.com/shorts/{c['youtube_video_id']})")
            if c.get("instagram_url"):
                links.append(f"[🟣 Instagram Reel]({c['instagram_url']})")
            if c.get("facebook_url"):
                links.append(f"[🔵 Facebook Reel]({c['facebook_url']})")
            link_str = " • ".join(links) or "Processing"
            time_str = c.get("created_at_12h") or c["created_at"]
            md.append(f"| **{c['title']}** | {time_str} | {link_str} |")
    else:
        md.append("> *No uploaded clips recorded in local database yet.*")
    md.append("")

    return "\n".join(md)


def render_html_dashboard(data: dict[str, Any]) -> str:
    yt = data["schedules"]["YouTube"]
    ig = data["schedules"]["Instagram"]
    fb = data["schedules"]["Facebook"]
    job = data.get("active_job", {})
    pct = job.get("percent", 100)
    msg = job.get("progress_message", "Standby")
    title = job.get("title", "Ready")

    clips_html = ""
    if data["uploaded_clips"]:
        for c in data["uploaded_clips"]:
            yt_link = (
                f'<a href="https://www.youtube.com/shorts/{c["youtube_video_id"]}" target="_blank" class="badge red">YouTube Short</a>'
                if c.get("youtube_video_id")
                else ""
            )
            ig_link = (
                f'<a href="{c["instagram_url"]}" target="_blank" class="badge pink">Instagram Reel</a>'
                if c.get("instagram_url")
                else ""
            )
            fb_link = (
                f'<a href="{c["facebook_url"]}" target="_blank" class="badge blue">Facebook Reel</a>'
                if c.get("facebook_url")
                else ""
            )
            clips_html += f"""
            <tr>
              <td><strong>{html.escape(c["title"])}</strong><div class="meta">{c["created_at"][:19].replace('T', ' ')} (Job: {c["job_id"]})</div></td>
              <td>{yt_link} {ig_link} {fb_link}</td>
            </tr>
            """
    else:
        clips_html = '<tr><td colspan="2" style="text-align:center; color:#94a3b8; padding:2rem;">No uploaded clips recorded in local database yet. Next scheduled run will populate live clips here.</td></tr>'

    channels_html = ""
    for ch in data["channels"]:
        channels_html += f'<span class="channel-tag">📺 {html.escape(ch.get("name") or "Channel")}</span> '

    pending_html = ""
    if data["pending_links"]:
        for p in data["pending_links"]:
            pending_html += f'<li><a href="{p}" target="_blank">{p}</a></li>'
    else:
        pending_html = '<li style="color:#64748b;">Queue is clear. Autopilot is scanning creator channels every 15m.</li>'

    spool_html = ""
    if data.get("spool_files"):
        for sf in data["spool_files"]:
            spool_html += f'<li style="color:#38bdf8;">📁 {html.escape(sf)} (queued for ingest)</li>'
    else:
        spool_html = '<li style="color:#64748b;">Dropzone empty. Drop any MP4/MOV into <code>queue/</code> or upload below.</li>'

    yt_prep_badge = '<span class="prep-badge">⚡ Auto-Prep Active</span>' if yt["prep_due"] else '<span class="status-badge">Standby</span>'
    ig_prep_badge = '<span class="prep-badge">⚡ Auto-Prep Active</span>' if ig["prep_due"] else '<span class="status-badge">Standby</span>'
    fb_prep_badge = '<span class="prep-badge">⚡ Auto-Prep Active</span>' if fb["prep_due"] else '<span class="status-badge">Standby</span>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="10">
  <title>AUTO-POSTER Live Mission Control</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #151c2e;
      --card-hover: #1e293b;
      --border: #232f48;
      --text: #f1f5f9;
      --subtext: #94a3b8;
      --primary: #38bdf8;
      --primary-hover: #0ea5e9;
      --green: #22c55e;
      --red: #ef4444;
      --pink: #ec4899;
      --blue: #3b82f6;
      --amber: #f59e0b;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 1.5rem;
      line-height: 1.5;
    }}
    .container {{ max-width: 1280px; margin: 0 auto; }}
    header {{
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1rem;
      background: var(--card-bg); padding: 1.25rem 2rem; border-radius: 14px;
      border: 1px solid var(--border); margin-bottom: 1.5rem;
      box-shadow: 0 4px 20px rgba(0,0,0,0.3);
    }}
    h1 {{ font-size: 1.6rem; color: var(--primary); display: flex; align-items: center; gap: 0.6rem; }}
    .live-dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--green); box-shadow: 0 0 10px var(--green); animation: pulse 2s infinite; display: inline-block; }}
    @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} 100% {{ opacity: 1; }} }}
    .time-badge {{ font-size: 0.85rem; color: var(--subtext); margin-top: 0.2rem; }}
    
    .status-ribbon {{ display: flex; gap: 0.5rem; flex-wrap: wrap; }}
    .status-pill {{
      display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.35rem 0.75rem;
      border-radius: 9999px; font-size: 0.75rem; font-weight: 600;
      background: #0f172a; border: 1px solid var(--border); color: #cbd5e1;
    }}
    .status-pill.ready {{ border-color: rgba(34, 197, 94, 0.4); color: #86efac; }}
    
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 1.25rem; margin-bottom: 1.5rem; }}
    .card {{
      background: var(--card-bg); border-radius: 14px; border: 1px solid var(--border);
      padding: 1.25rem; box-shadow: 0 4px 15px rgba(0,0,0,0.2);
    }}
    .card h2 {{ font-size: 1.15rem; margin-bottom: 0.85rem; color: #e2e8f0; display: flex; justify-content: space-between; align-items: center; }}
    
    /* Interactive Actions Bar */
    .actions-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 1.25rem; margin-bottom: 1.5rem; }}
    .action-box {{
      background: #111827; border: 1px dashed var(--border); border-radius: 12px;
      padding: 1.25rem; text-align: center; transition: all 0.2s ease;
    }}
    .action-box:hover {{ border-color: var(--primary); background: #131d33; }}
    .dropzone {{
      border: 2px dashed #334155; border-radius: 10px; padding: 1.5rem 1rem;
      cursor: pointer; transition: all 0.2s ease; background: #0f172a;
    }}
    .dropzone:hover, .dropzone.dragover {{ border-color: var(--primary); background: #1a253d; }}
    .dropzone-icon {{ font-size: 2rem; margin-bottom: 0.5rem; display: block; }}
    
    input[type="text"] {{
      width: 100%; padding: 0.65rem 0.9rem; border-radius: 8px;
      background: #0f172a; border: 1px solid var(--border); color: #f1f5f9;
      font-size: 0.9rem; margin-bottom: 0.6rem; outline: none;
    }}
    input[type="text"]:focus {{ border-color: var(--primary); }}
    .btn {{
      display: inline-block; padding: 0.6rem 1.2rem; border-radius: 8px;
      font-weight: 600; font-size: 0.85rem; cursor: pointer; border: none;
      transition: all 0.2s ease; text-decoration: none;
    }}
    .btn-primary {{ background: var(--primary); color: #0f172a; width: 100%; }}
    .btn-primary:hover {{ background: var(--primary-hover); }}
    .btn-secondary {{ background: #1e293b; color: #f1f5f9; border: 1px solid var(--border); }}
    .btn-secondary:hover {{ background: #334155; }}
    
    .slot-row {{ display: flex; justify-content: space-between; margin: 0.5rem 0; font-size: 0.95rem; }}
    .slot-highlight {{ font-size: 1.5rem; font-weight: 800; color: var(--primary); margin: 0.4rem 0; }}
    .slots-list {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.75rem; }}
    .slot-pill {{ background: #1e293b; padding: 0.2rem 0.5rem; border-radius: 6px; font-size: 0.75rem; color: #cbd5e1; border: 1px solid #334155; }}
    
    .badge {{ display: inline-block; padding: 0.35rem 0.75rem; border-radius: 6px; font-size: 0.75rem; font-weight: 600; text-decoration: none; color: #fff; margin-right: 0.3rem; }}
    .badge.red {{ background: #dc2626; }}
    .badge.pink {{ background: #db2777; }}
    .badge.blue {{ background: #2563eb; }}
    .prep-badge {{ background: var(--amber); color: #000; font-size: 0.75rem; font-weight: bold; padding: 0.25rem 0.55rem; border-radius: 4px; }}
    .status-badge {{ background: #334155; font-size: 0.75rem; padding: 0.25rem 0.55rem; border-radius: 4px; color: #94a3b8; }}

    table {{ width: 100%; border-collapse: collapse; margin-top: 0.5rem; }}
    th, td {{ text-align: left; padding: 0.85rem 1rem; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--subtext); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .meta {{ font-size: 0.75rem; color: var(--subtext); margin-top: 0.2rem; }}
    
    .channel-tag {{ display: inline-block; background: #1e293b; padding: 0.3rem 0.7rem; border-radius: 6px; font-size: 0.8rem; margin: 0.25rem; border: 1px solid var(--border); }}
    ul.queue {{ padding-left: 1.25rem; font-size: 0.85rem; line-height: 1.7; }}
    ul.queue a {{ color: var(--primary); text-decoration: none; }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1><span class="live-dot"></span> AUTO-POSTER Live Mission Control</h1>
        <div class="time-badge">Day: <strong>{data["today_name"]}</strong> | Server Time (IST): <strong>{data["timestamp"]}</strong> | Auto-Refreshes: 10s</div>
      </div>
      <div class="status-ribbon">
        <span class="status-pill ready">🔴 YouTube: <strong>Splitzzz</strong></span>
        <span class="status-pill ready">🟣 IG: <strong>@splitzz.isodope</strong></span>
        <span class="status-pill ready">🔵 FB: <strong>Splitzz</strong></span>
        <span class="status-pill ready">⚡ Groq: <strong>Whisper v3</strong></span>
      </div>
    </header>

    <!-- Glowing Progress Bar Card -->
    <div class="card" style="margin-bottom: 1.5rem; background: linear-gradient(135deg, #151c2e, #1e293b); border: 1px solid #38bdf8;">
      <h2><span>⚡ LIVE PIPELINE & VIDEO CUTTING PROGRESS</span> <span style="color:var(--primary); font-weight:bold;">{pct}% Complete</span></h2>
      <div style="background: #0f172a; border-radius: 9999px; height: 26px; padding: 3px; border: 1px solid #334155; margin: 1rem 0; overflow: hidden; position: relative;">
        <div style="background: linear-gradient(90deg, #38bdf8, #818cf8, #ec4899); height: 100%; border-radius: 9999px; width: {pct}%; transition: width 0.5s ease-in-out; box-shadow: 0 0 15px rgba(56, 189, 248, 0.6); display: flex; align-items: center; justify-content: flex-end; padding-right: 10px; font-weight: bold; font-size: 0.75rem; color: #fff;">{pct}%</div>
      </div>
      <div style="display: flex; justify-content: space-between; font-size: 0.85rem; color: var(--subtext); flex-wrap: wrap; gap: 0.5rem;">
        <span>🎬 <strong>Current Action:</strong> <strong style="color: #f1f5f9;">{html.escape(msg)}</strong></span>
        <span>📹 <strong>Source Content:</strong> <strong style="color: #38bdf8;">{html.escape(title)}</strong></span>
      </div>
    </div>

    <!-- Quick Action / Interactive Dropzone & Link Input -->
    <div class="actions-grid">
      <!-- Drag & Drop Spooler Box -->
      <div class="action-box">
        <h3 style="font-size: 1rem; margin-bottom: 0.5rem; color: #e2e8f0;">📁 Drag & Drop Video Spooler</h3>
        <p style="font-size: 0.8rem; color: var(--subtext); margin-bottom: 0.8rem;">Drop any video (.mp4, .mov) to auto-split with gameplay & schedule</p>
        <div class="dropzone" id="dropzone" onclick="document.getElementById('fileInput').click()">
          <span class="dropzone-icon">📥</span>
          <span id="dropzoneText" style="font-size: 0.85rem; color: #94a3b8;">Click or drag & drop video file here</span>
          <input type="file" id="fileInput" accept="video/mp4,video/quicktime,video/x-matroska,video/webm" style="display:none;" onchange="uploadFile(this.files[0])">
        </div>
      </div>

      <!-- Quick YouTube Queue Box -->
      <div class="action-box">
        <h3 style="font-size: 1rem; margin-bottom: 0.5rem; color: #e2e8f0;">🔗 Queue YouTube Video</h3>
        <p style="font-size: 0.8rem; color: var(--subtext); margin-bottom: 0.8rem;">Paste YouTube link to download, split, and multi-post</p>
        <form onsubmit="queueUrl(event)">
          <input type="text" id="ytUrlInput" placeholder="https://www.youtube.com/watch?v=..." required>
          <button type="submit" class="btn btn-primary" id="queueBtn">⚡ Queue URL Now</button>
        </form>
      </div>

      <!-- Autopilot Trigger Box -->
      <div class="action-box" style="display:flex; flex-direction:column; justify-content:space-between;">
        <div>
          <h3 style="font-size: 1rem; margin-bottom: 0.5rem; color: #e2e8f0;">🤖 Autopilot Channel Scanner</h3>
          <p style="font-size: 0.8rem; color: var(--subtext); margin-bottom: 0.8rem;">Scans 12 creator RSS feeds (MrBeast, CarryMinati, Speed...) with zero quota</p>
        </div>
        <button class="btn btn-secondary" onclick="triggerAutopilot()" id="scanBtn" style="padding: 0.8rem; width:100%;">📡 Scan Channels Right Now</button>
      </div>
    </div>

    <!-- Scheduled Slots Grid -->
    <div class="grid">
      <div class="card">
        <h2><span>📺 YouTube Shorts</span> {yt_prep_badge}</h2>
        <div class="slot-row"><span>Next Target Window:</span></div>
        <div class="slot-highlight">{yt["next_slot"]}</div>
        <div class="slot-row" style="color:var(--subtext); font-size:0.8rem;">Auto-Prep runs 60 mins before slot.</div>
        <div style="margin-top:0.75rem; font-size:0.8rem; color:var(--subtext);">Today's {len(yt["slots_today"])} Scheduled Slots:</div>
        <div class="slots-list">
          {''.join(f'<span class="slot-pill">{s}</span>' for s in yt["slots_today"])}
        </div>
      </div>

      <div class="card">
        <h2><span>📸 Instagram Reels</span> {ig_prep_badge}</h2>
        <div class="slot-row"><span>Next Target Window:</span></div>
        <div class="slot-highlight">{ig["next_slot"]}</div>
        <div class="slot-row" style="color:var(--subtext); font-size:0.8rem;">Auto-Prep runs 60 mins before slot.</div>
        <div style="margin-top:0.75rem; font-size:0.8rem; color:var(--subtext);">Today's {len(ig["slots_today"])} Scheduled Slots:</div>
        <div class="slots-list">
          {''.join(f'<span class="slot-pill">{s}</span>' for s in ig["slots_today"])}
        </div>
      </div>

      <div class="card">
        <h2><span>👥 Facebook Reels</span> {fb_prep_badge}</h2>
        <div class="slot-row"><span>Next Target Window:</span></div>
        <div class="slot-highlight">{fb["next_slot"]}</div>
        <div class="slot-row" style="color:var(--subtext); font-size:0.8rem;">Auto-Prep runs 60 mins before slot.</div>
        <div style="margin-top:0.75rem; font-size:0.8rem; color:var(--subtext);">Today's {len(fb["slots_today"])} Scheduled Slots:</div>
        <div class="slots-list">
          {''.join(f'<span class="slot-pill">{s}</span>' for s in fb["slots_today"])}
        </div>
      </div>
    </div>

    <!-- Uploaded Videos Table -->
    <div class="card" style="margin-bottom: 1.5rem;">
      <h2><span>🚀 Published Content & Live Links</span> <span style="font-size:0.8rem; color:var(--subtext);">Recent Multi-Platform Uploads</span></h2>
      <table>
        <thead>
          <tr>
            <th>Video Title & Timestamp</th>
            <th>Direct Platform Links</th>
          </tr>
        </thead>
        <tbody>
          {clips_html}
        </tbody>
      </table>
    </div>

    <!-- Queues & Monitored Creators -->
    <div class="grid">
      <div class="card">
        <h2><span>📡 Monitored Creators ({len(data["channels"])})</span></h2>
        <div style="margin-top:0.5rem;">
          {channels_html}
        </div>
      </div>

      <div class="card">
        <h2><span>📋 Staging Queues</span></h2>
        <div style="margin-top:0.5rem;">
          <strong style="font-size:0.8rem; color:var(--primary);">links.txt ({len(data["pending_links"])} items):</strong>
          <ul class="queue" style="margin: 0.3rem 0 1rem 0;">
            {pending_html}
          </ul>
          <strong style="font-size:0.8rem; color:var(--green);">queue/ dropzone ({len(data.get("spool_files", []))} items):</strong>
          <ul class="queue" style="margin-top: 0.3rem;">
            {spool_html}
          </ul>
        </div>
      </div>
    </div>
  </div>

  <script>
    const dropzone = document.getElementById('dropzone');
    dropzone.addEventListener('dragover', (e) => {{ e.preventDefault(); dropzone.classList.add('dragover'); }});
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
    dropzone.addEventListener('drop', (e) => {{
      e.preventDefault();
      dropzone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {{
        uploadFile(e.dataTransfer.files[0]);
      }}
    }});

    async function uploadFile(file) {{
      if (!file) return;
      const text = document.getElementById('dropzoneText');
      text.innerText = `⏳ Uploading ${{file.name}}...`;
      try {{
        const res = await fetch(`/api/upload?filename=${{encodeURIComponent(file.name)}}`, {{
          method: 'POST',
          body: file
        }});
        const json = await res.json();
        if (json.status === 'ok') {{
          text.innerText = `✅ Ingested ${{file.name}}! Processing...`;
          setTimeout(() => location.reload(), 1500);
        }} else {{
          text.innerText = `❌ Error: ${{json.error || 'Upload failed'}}`;
        }}
      }} catch (err) {{
        text.innerText = `❌ Upload failed: ${{err.message}}`;
      }}
    }}

    async function queueUrl(e) {{
      e.preventDefault();
      const input = document.getElementById('ytUrlInput');
      const btn = document.getElementById('queueBtn');
      const val = input.value.trim();
      if (!val) return;
      btn.disabled = true;
      btn.innerText = '⏳ Queuing...';
      try {{
        const res = await fetch('/api/queue-link', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ url: val }})
        }});
        const json = await res.json();
        if (json.status === 'ok') {{
          btn.innerText = '✅ Queued!';
          input.value = '';
          setTimeout(() => location.reload(), 1200);
        }} else {{
          btn.innerText = `❌ ${{json.error || 'Failed'}}`;
        }}
      }} catch (err) {{
        btn.innerText = '❌ Request failed';
      }} finally {{
        setTimeout(() => {{ btn.disabled = false; btn.innerText = '⚡ Queue URL Now'; }}, 3000);
      }}
    }}

    async function triggerAutopilot() {{
      const btn = document.getElementById('scanBtn');
      btn.disabled = true;
      btn.innerText = '⏳ Scanning RSS Feeds...';
      try {{
        const res = await fetch('/api/scan-autopilot', {{ method: 'POST' }});
        const json = await res.json();
        btn.innerText = `✅ Found ${{json.new_videos_count || 0}} new uploads!`;
        setTimeout(() => location.reload(), 1500);
      }} catch (err) {{
        btn.innerText = '❌ Scan failed';
      }} finally {{
        setTimeout(() => {{ btn.disabled = false; btn.innerText = '📡 Scan Channels Right Now'; }}, 3000);
      }}
    }}
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_path = urllib.parse.urlparse(self.path)

        # 0. Dedicated lightweight keep-alive / healthcheck endpoint
        if parsed_path.path in ("/healthz", "/ping", "/health"):
            db_file = Path(os.environ.get("DATABASE_PATH", "work/jobs.db"))
            payload = {
                "status": "ok",
                "service": "money-printer",
                "timestamp": datetime.now().isoformat(),
                "database": "connected" if db_file.exists() else "initializing",
                "keep_alive": True,
            }
            self._send_json(payload, status=200)
            return

        settings = Settings.from_env()

        if parsed_path.path == "/api/data":
            data = get_dashboard_data(settings.database_path, settings)
            self._send_json(data)
            return

        if parsed_path.path == "/api/health":
            health = asyncio.run(get_system_health(settings))
            self._send_json(health)
            return

        data = get_dashboard_data(settings.database_path, settings)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        html_content = render_html_dashboard(data)
        self.wfile.write(html_content.encode("utf-8"))

    def do_POST(self) -> None:
        settings = Settings.from_env()
        parsed_path = urllib.parse.urlparse(self.path)

        # 1. API: Queue YouTube URL
        if parsed_path.path == "/api/queue-link":
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length).decode("utf-8")
            try:
                body = json.loads(raw_body)
                url = body.get("url", "").strip()
                if not is_youtube_url(url):
                    self._send_json({"status": "error", "error": "Invalid YouTube URL"}, 400)
                    return
                # Append to links.txt
                with open(settings.links_file, "a", encoding="utf-8") as f:
                    f.write(f"\n{url}\n")
                self._send_json({"status": "ok", "url": url})
            except Exception as exc:
                self._send_json({"status": "error", "error": str(exc)}, 500)
            return

        # 2. API: Trigger Autopilot Scan
        if parsed_path.path == "/api/scan-autopilot":
            try:
                repo = JobRepository(settings.database_path)
                autopilot = AutopilotService.from_settings(settings, repo)
                queued = asyncio.run(autopilot.check_and_queue(settings.links_file, settings.downloaded_links_log))
                self._send_json({"status": "ok", "new_videos_count": len(queued)})
            except Exception as exc:
                self._send_json({"status": "error", "error": str(exc)}, 500)
            return

        # 3. API: Binary Upload to queue/
        if parsed_path.path == "/api/upload":
            query = urllib.parse.parse_qs(parsed_path.query)
            filename = query.get("filename", [f"upload_{int(datetime.now().timestamp())}.mp4"])[0]
            # Sanitize filename
            filename = "".join(c for c in filename if c.isalnum() or c in "._- ")
            queue_dir = Path("queue")
            queue_dir.mkdir(parents=True, exist_ok=True)
            target_path = queue_dir / filename

            length = int(self.headers.get("Content-Length", 0))
            try:
                with open(target_path, "wb") as f:
                    remaining = length
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 65536))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                self._send_json({"status": "ok", "saved_path": str(target_path)})
            except Exception as exc:
                self._send_json({"status": "error", "error": str(exc)}, 500)
            return

        self._send_json({"error": "Endpoint not found"}, 404)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Quiet logging


def run_dashboard(port: int = 5050) -> None:
    server = ThreadingHTTPServer(("0.0.0.0", port), DashboardHandler)
    print("=" * 60)
    print(f"🚀 AUTO-POSTER Mission Control Live at: http://localhost:{port}")
    print("=" * 60)
    server.serve_forever()


if __name__ == "__main__":
    run_dashboard()
