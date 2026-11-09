from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .config import Settings
from .db import JobRepository
from .models import Job, JobStatus, SourceVideo
from .pipeline import WorkflowPipeline, WorkflowServices

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}


class SpoolWatcher:
    """
    Spool / Watch folder monitor inspired by spoolcast and PostFlow.
    Watches the 'queue/' directory for dropped video files, auto-ingests them,
    and runs them through the split-screen and multi-platform publishing pipeline.
    """

    def __init__(
        self,
        queue_dir: Path,
        repository: JobRepository,
        services: WorkflowServices,
        settings: Settings,
    ) -> None:
        self.queue_dir = queue_dir
        self.processed_dir = queue_dir / "processed"
        self.failed_dir = queue_dir / "failed"
        self.repository = repository
        self.services = services
        self.settings = settings
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def scan_new_files(self) -> list[Path]:
        """Finds video files dropped in queue/ that are ready for processing."""
        if not self.queue_dir.exists():
            return []
        
        candidates: list[Path] = []
        for entry in self.queue_dir.iterdir():
            if not entry.is_file():
                continue
            if entry.name.startswith(".") or entry.name.startswith("~"):
                continue
            if entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                if self._is_file_ready(entry):
                    candidates.append(entry)
        return sorted(candidates, key=lambda p: p.stat().st_mtime)

    def _is_file_ready(self, file_path: Path) -> bool:
        """Verifies the file is completely written and not locked by another process."""
        try:
            initial_size = file_path.stat().st_size
            if initial_size == 0:
                return False
            time.sleep(0.5)
            final_size = file_path.stat().st_size
            if initial_size != final_size:
                return False
            # Test open for reading
            with open(file_path, "rb") as f:
                f.read(1024)
            return True
        except (OSError, PermissionError):
            return False

    async def ingest_file(self, video_path: Path, pipeline: WorkflowPipeline) -> Job:
        """
        Ingests a dropped video file into a new job and executes the pipeline.
        """
        job_id = f"spool-{uuid.uuid4().hex[:8]}"
        job_dir = self.settings.work_dir / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"[Spooler] Ingesting dropped video: {video_path.name} -> Job {job_id}")

        # Check for sidecar instructions (.txt or .json)
        sidecar_txt = video_path.with_suffix(".txt")
        sidecar_json = video_path.with_suffix(".json")
        custom_title = video_path.stem.replace("_", " ").replace("-", " ").title()
        custom_caption = ""

        if sidecar_txt.exists():
            try:
                custom_caption = sidecar_txt.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        if sidecar_json.exists():
            try:
                sidecar_data = json.loads(sidecar_json.read_text(encoding="utf-8"))
                custom_title = sidecar_data.get("title", custom_title)
                custom_caption = sidecar_data.get("caption", custom_caption)
            except Exception:
                pass

        # Copy file into job destination as source.mp4
        dest_source = job_dir / f"source{video_path.suffix.lower()}"
        shutil.copy2(video_path, dest_source)

        # Probe media duration
        duration = 30.0
        try:
            duration = self.services.media.probe_duration(dest_source)
        except Exception as exc:
            logger.warning(f"[Spooler] Could not probe exact duration: {exc}")

        # Write source.info.json for pipeline discovery
        info_json = job_dir / "source.info.json"
        info_payload = {
            "id": job_id,
            "title": custom_title,
            "uploader": "Local Dropzone",
            "duration": duration,
            "extractor": "local_spooler",
        }
        info_json.write_text(json.dumps(info_payload, indent=2), encoding="utf-8")

        # Create job in database
        job = self.repository.create(
            job_id=job_id,
            source_url=f"local://{video_path.name}",
            source_title=custom_title,
            output_path=None,
            short_title=custom_title,
            short_description=custom_caption or None,
            instagram_caption=custom_caption or None,
        )

        try:
            completed_job = await pipeline.process(job.id, reuse_downloaded=True)
            # Move processed files
            target_dest = self.processed_dir / video_path.name
            if target_dest.exists():
                target_dest.unlink(missing_ok=True)
            shutil.move(str(video_path), str(target_dest))

            if sidecar_txt.exists():
                sidecar_txt.rename(self.processed_dir / sidecar_txt.name)
            if sidecar_json.exists():
                sidecar_json.rename(self.processed_dir / sidecar_json.name)

            logger.info(f"[Spooler] Successfully processed and published job {job_id}")
            return completed_job

        except Exception as exc:
            logger.error(f"[Spooler] Failed processing {video_path.name}: {exc}", exc_info=True)
            # Move to failed directory with error note
            failed_dest = self.failed_dir / video_path.name
            if failed_dest.exists():
                failed_dest.unlink(missing_ok=True)
            shutil.move(str(video_path), str(failed_dest))
            (self.failed_dir / f"{video_path.stem}.error.txt").write_text(str(exc), encoding="utf-8")
            raise

    async def poll_once(self, pipeline: WorkflowPipeline) -> int:
        """Polls queue/ and processes any dropped videos."""
        ready_files = self.scan_new_files()
        processed_count = 0
        for f in ready_files:
            try:
                await self.ingest_file(f, pipeline)
                processed_count += 1
            except Exception as exc:
                logger.error(f"[Spooler] Error during ingest of {f.name}: {exc}")
        return processed_count

