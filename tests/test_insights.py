from pathlib import Path

from shorts_bot.db import JobRepository
from shorts_bot.insights import render_report, sync_youtube_insights
from shorts_bot.models import ShortPlan


class _FakeRequest:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def execute(self) -> dict:  # noqa: ANN201
        return self.payload


class _FakeVideos:
    def __init__(self, statistics_by_id: dict[str, dict[str, str]]) -> None:
        self.statistics_by_id = statistics_by_id
        self.requested_ids: list[str] = []

    def list(self, part: str, id: str):  # noqa: A002, ANN001, ANN201
        assert part == "statistics,snippet"
        requested = [item for item in id.split(",") if item]
        self.requested_ids.extend(requested)
        items = [
            {
                "id": video_id,
                "snippet": {"title": f"Title for {video_id}"},
                "statistics": self.statistics_by_id[video_id],
            }
            for video_id in requested
            if video_id in self.statistics_by_id
        ]
        return _FakeRequest({"items": items})


class _FakeYouTubeService:
    def __init__(self, statistics_by_id: dict[str, dict[str, str]]) -> None:
        self._videos = _FakeVideos(statistics_by_id)
        self.statistics_by_id = statistics_by_id

    def videos(self) -> _FakeVideos:
        return self._videos


def _repo_with_uploads(tmp_path: Path, video_ids: list[str]) -> JobRepository:
    repository = JobRepository(tmp_path / "jobs.db")
    job = repository.create(chat_id=0, user_id=0, source_url="https://youtube.com/watch?v=abc")
    plans = [
        ShortPlan(
            start_seconds=float(i),
            duration_seconds=30.0,
            title=f"Clip {i}",
            description="Description",
            instagram_caption="Caption",
        )
        for i in range(len(video_ids))
    ]
    repository.save_plans(job.id, plans)
    for index, video_id in enumerate(video_ids, start=1):
        repository.update_clip(job.id, index, youtube_video_id=video_id)
    return repository


def test_sync_caches_statistics_and_is_idempotent(tmp_path: Path) -> None:
    repository = _repo_with_uploads(tmp_path, ["vid-1", "vid-2"])
    service = _FakeYouTubeService(
        {
            "vid-1": {"viewCount": "1200", "likeCount": "45", "commentCount": "9"},
            "vid-2": {"viewCount": "300", "likeCount": "12", "commentCount": "2"},
        }
    )

    summary = sync_youtube_insights(
        repository, tmp_path / "token.json", service_factory=lambda _path: service
    )

    assert summary == {"requested": 2, "found": 2, "written": 2, "missing": 0}
    stats = repository.list_video_stats()
    assert [row["video_id"] for row in stats] == ["vid-1", "vid-2"]
    assert stats[0]["views"] == 1200
    assert stats[0]["likes"] == 45
    assert stats[0]["comments"] == 9

    # Second sync updates rather than duplicates
    service.statistics_by_id["vid-1"] = {
        "viewCount": "5000",
        "likeCount": "80",
        "commentCount": "15",
    }
    summary = sync_youtube_insights(
        repository, tmp_path / "token.json", service_factory=lambda _path: service
    )
    assert summary["written"] == 2
    stats = repository.list_video_stats()
    assert len(stats) == 2
    assert stats[0]["views"] == 5000


def test_sync_reports_missing_videos(tmp_path: Path) -> None:
    repository = _repo_with_uploads(tmp_path, ["vid-1", "gone-1"])
    service = _FakeYouTubeService(
        {"vid-1": {"viewCount": "10", "likeCount": "1", "commentCount": "0"}}
    )

    summary = sync_youtube_insights(
        repository, tmp_path / "token.json", service_factory=lambda _path: service
    )

    assert summary["requested"] == 2
    assert summary["found"] == 1
    assert summary["missing"] == 1


def test_sync_without_uploads_is_noop(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.db")

    summary = sync_youtube_insights(
        repository,
        tmp_path / "token.json",
        service_factory=lambda _path: (_ for _ in ()).throw(AssertionError("no service expected")),
    )

    assert summary == {"requested": 0, "found": 0, "written": 0, "missing": 0}


def test_render_report_formats_totals(tmp_path: Path) -> None:
    repository = _repo_with_uploads(tmp_path, ["vid-1"])
    service = _FakeYouTubeService(
        {"vid-1": {"viewCount": "42", "likeCount": "7", "commentCount": "3"}}
    )
    sync_youtube_insights(
        repository, tmp_path / "token.json", service_factory=lambda _path: service
    )

    report = render_report(repository.list_video_stats())

    assert "Views" in report
    assert "TOTAL (1 videos)" in report
    assert "42" in report
    assert render_report([]).startswith("No cached YouTube statistics")
