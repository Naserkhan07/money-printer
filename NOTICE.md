# NOTICE — Attribution and third-party code

Money Printer is based on and interoperates with work from the following projects.
Each license below is reproduced or referenced as required.

## Base project (user's own code)

- **Naserkhan07/AUTO-POSTER** — https://github.com/Naserkhan07/AUTO-POSTER
  The processing pipeline, renderer, slot scheduler, Autopilot, local dashboard,
  Docker image, GitHub Actions workflow, and test suite originate here.

## Ported code (MIT License)

### HillzKillzIt/social-media-scheduler
https://github.com/HillzKillzIt/social-media-scheduler — MIT License
Copyright (c) 2026 HillzKillzIt

Ported into:
- `shorts_bot/retry.py` — `with_retry` decorator (exponential backoff with ±20% jitter),
  adapted to this project's logging style.
- `shorts_bot/tiktok.py` — TikTok Content Posting API flow (init → chunked upload →
  status poll), rewritten to async httpx against the official Direct Post endpoints.

### Jayrome Vergara — jaycverg/spoolcast
https://github.com/jaycverg/spoolcast — MIT License
Copyright (c) 2026 Jayrome Vergara

Ported into:
- `shorts_bot/facebook.py` — Facebook Reels publishing with
  `video_state=SCHEDULED` and `scheduled_publish_time`, including the
  "wait for processing, not for publication" polling behavior for scheduled Reels.

### Abhinavkumar-07 — youtube-instagram-auto-uploader
https://github.com/Abhinavkumar-07/youtube-instagram-auto-uploader — MIT License
Copyright (c) 2026 Abhinav kumar

Pattern referenced (no verbatim code) in:
- `shorts_bot/youtube.py` — uploading as `private` with `publishAt` so the platform
  flips the video public at the requested time; and
- the `SCHEDULED_PUBLISH_LEAD_MINUTES` design (hand the publish time to the platform
  instead of relying on a local timer staying alive).

## Concepts referenced only (no substantial code copied)

- **Liparoto/Social-Scheduler** — https://github.com/Liparoto/Social-Scheduler —
  the "insights hub" idea (per-post performance, cached in the local database).
  Reimplemented in Python + SQLite in `shorts_bot/insights.py`.
- **notherobot/social-media-scheduler (PostFlow)** — https://github.com/notherobot/social-media-scheduler —
  publishing-history presentation concept reflected in the dashboard's
  Published Performance panel.
- **Masterjx9/socialmediascheduler** — https://github.com/Masterjx9/socialmediascheduler —
  account/channel configuration patterns. No license file; no code taken.
- **arillera/meta-scheduler** — https://github.com/arillera/meta-scheduler — MIT License.
  Evaluated and intentionally not adopted (Playwright-driven Meta Business Suite
  automation; this project uses the official Graph APIs).
- **modernzen/forges** — https://github.com/modernzen/forges — MIT License (Copyright 2024 Late).
  Evaluated and intentionally not adopted (unified wrapper for the paid Late API).

## Full text of the MIT License (for the ports above)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
