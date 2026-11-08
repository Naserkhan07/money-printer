import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from shorts_bot.autopilot import AutopilotChannel, AutopilotService, AutopilotVideo
from shorts_bot.config import Settings
from shorts_bot.db import JobRepository

_SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
  <title>Creator Channel</title>
  <entry>
    <id>yt:video:video123</id>
    <yt:videoId>video123</yt:videoId>
    <yt:channelId>UC1234567890123456789012</yt:channelId>
    <title>Awesome New Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=video123"/>
    <published>2026-10-30T10:00:00+00:00</published>
  </entry>
  <entry>
    <id>yt:video:short456</id>
    <yt:videoId>short456</yt:videoId>
    <yt:channelId>UC1234567890123456789012</yt:channelId>
    <title>Quick Short Clip</title>
    <link rel="alternate" href="https://www.youtube.com/shorts/short456"/>
    <published>2026-10-29T10:00:00+00:00</published>
  </entry>
</feed>
"""


class TestAutopilot(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        import gc
        gc.collect()
        self.temp_dir.cleanup()

    def test_parses_channel_feed_xml(self) -> None:
        repository = JobRepository(self.tmp_path / "jobs.db")
        channel = AutopilotChannel(name="Test Creator", channel_id="UC1234567890123456789012")
        service = AutopilotService(channels=[channel], repository=repository)

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = _SAMPLE_FEED.encode("utf-8")
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            videos = service.fetch_channel_videos(channel)

        self.assertEqual(len(videos), 2)
        self.assertEqual(videos[0].video_id, "video123")
        self.assertEqual(videos[0].title, "Awesome New Video")
        self.assertFalse(videos[0].is_short)
        self.assertEqual(videos[0].watch_url, "https://www.youtube.com/watch?v=video123")

        self.assertEqual(videos[1].video_id, "short456")
        self.assertTrue(videos[1].is_short)

    def test_queue_videos_appends_to_links_file(self) -> None:
        repository = JobRepository(self.tmp_path / "jobs.db")
        links_file = self.tmp_path / "links.txt"
        links_file.write_text("# existing\n", encoding="utf-8")

        service = AutopilotService(channels=[], repository=repository)
        video = AutopilotVideo(
            video_id="xyz999",
            title="Epic Video",
            published_at="2026-10-30T12:00:00+00:00",
            link="https://www.youtube.com/watch?v=xyz999",
            is_short=False,
            channel_name="Creator",
            channel_id="UC1111111111111111111111",
        )

        queued = service.queue_videos([video], links_file)
        self.assertEqual(queued, ["https://www.youtube.com/watch?v=xyz999"])
        self.assertTrue(repository.is_autopilot_seen("xyz999"))

        content = links_file.read_text(encoding="utf-8")
        self.assertIn("https://www.youtube.com/watch?v=xyz999", content)
        self.assertIn("# [Autopilot: Creator] Epic Video", content)

    def test_bootstrap_baseline_without_queueing(self) -> None:
        repository = JobRepository(self.tmp_path / "jobs.db")
        links_file = self.tmp_path / "links.txt"
        channel = AutopilotChannel(name="Test Creator", channel_id="UC1234567890123456789012")
        service = AutopilotService(
            channels=[channel],
            repository=repository,
            queue_latest_on_start=False,
        )

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = _SAMPLE_FEED.encode("utf-8")
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            queued_count = asyncio.run(service.bootstrap_baseline(links_file))

        self.assertEqual(queued_count, 0)
        self.assertTrue(repository.is_autopilot_seen("video123"))
        self.assertTrue(repository.is_autopilot_seen("short456"))
        self.assertTrue(not links_file.exists() or links_file.read_text() == "")

    def test_sync_latest_videos_queues_recent_per_channel(self) -> None:
        repository = JobRepository(self.tmp_path / "jobs.db")
        links_file = self.tmp_path / "links.txt"
        channel = AutopilotChannel(name="Test Creator", channel_id="UC1234567890123456789012")
        service = AutopilotService(
            channels=[channel],
            repository=repository,
            recent_per_channel=2,
        )

        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <title>Creator Channel</title>
          <entry>
            <id>yt:video:new1</id>
            <yt:videoId>new1</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Newest Video</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=new1"/>
            <published>2026-11-06T10:00:00+00:00</published>
          </entry>
          <entry>
            <id>yt:video:new2</id>
            <yt:videoId>new2</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Second Newest</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=new2"/>
            <published>2026-11-04T10:00:00+00:00</published>
          </entry>
          <entry>
            <id>yt:video:new3</id>
            <yt:videoId>new3</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Third Newest</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=new3"/>
            <published>2026-11-02T10:00:00+00:00</published>
          </entry>
        </feed>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = feed.encode("utf-8")
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            synced = service.sync_latest_videos(links_file)

        self.assertEqual([v.video_id for v in synced], ["new1", "new2"])
        content = links_file.read_text(encoding="utf-8")
        self.assertIn("watch?v=new1", content)
        self.assertIn("watch?v=new2", content)
        self.assertNotIn("watch?v=new3", content)
        self.assertTrue(repository.is_autopilot_seen("new1"))
        self.assertTrue(repository.is_autopilot_seen("new2"))
        self.assertTrue(repository.is_autopilot_seen("new3"))

    def test_check_for_new_videos_queues_recent_per_channel(self) -> None:
        repository = JobRepository(self.tmp_path / "jobs.db")
        links_file = self.tmp_path / "links.txt"
        channel = AutopilotChannel(name="Test Creator", channel_id="UC1234567890123456789012")
        service = AutopilotService(
            channels=[channel],
            repository=repository,
            recent_per_channel=2,
        )

        feed = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <title>Creator Channel</title>
          <entry>
            <id>yt:video:up3</id>
            <yt:videoId>up3</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Upload Three</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=up3"/>
            <published>2026-11-07T10:00:00+00:00</published>
          </entry>
          <entry>
            <id>yt:video:up2</id>
            <yt:videoId>up2</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Upload Two</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=up2"/>
            <published>2026-11-05T10:00:00+00:00</published>
          </entry>
          <entry>
            <id>yt:video:up1</id>
            <yt:videoId>up1</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Upload One</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=up1"/>
            <published>2026-11-01T10:00:00+00:00</published>
          </entry>
        </feed>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = feed.encode("utf-8")
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            new_videos = asyncio.run(service.check_for_new_videos(links_file))

        self.assertEqual([v.video_id for v in new_videos], ["up3", "up2"])
        self.assertTrue(repository.is_autopilot_seen("up1"))
        self.assertFalse(repository.is_autopilot_seen("up3"))
        self.assertFalse(repository.is_autopilot_seen("up2"))

    def test_detects_new_videos_after_baseline(self) -> None:
        repository = JobRepository(self.tmp_path / "jobs.db")
        links_file = self.tmp_path / "links.txt"
        channel = AutopilotChannel(name="Test Creator", channel_id="UC1234567890123456789012")
        service = AutopilotService(channels=[channel], repository=repository)

        repository.mark_autopilot_seen("video123", channel.channel_id, "Old Video")
        repository.mark_autopilot_seen("short456", channel.channel_id, "Old Short")

        updated_feed = """<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
          <title>Creator Channel</title>
          <entry>
            <id>yt:video:brand_new_video</id>
            <yt:videoId>brand_new_video</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Brand New Video</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=brand_new_video"/>
            <published>2026-10-30T15:00:00+00:00</published>
          </entry>
          <entry>
            <id>yt:video:video123</id>
            <yt:videoId>video123</yt:videoId>
            <yt:channelId>UC1234567890123456789012</yt:channelId>
            <title>Awesome New Video</title>
            <link rel="alternate" href="https://www.youtube.com/watch?v=video123"/>
            <published>2026-10-30T10:00:00+00:00</published>
          </entry>
        </feed>
        """

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = updated_feed.encode("utf-8")
            mock_response.__enter__.return_value = mock_response
            mock_urlopen.return_value = mock_response

            new_videos = asyncio.run(service.check_for_new_videos(links_file))

        self.assertEqual(len(new_videos), 1)
        self.assertEqual(new_videos[0].video_id, "brand_new_video")
        self.assertEqual(new_videos[0].title, "Brand New Video")

    def test_loads_autopilot_settings_from_channels_toml(self) -> None:
        toml_path = self.tmp_path / "channels.toml"
        toml_path.write_text(
            """
            [autopilot]
            enabled = true
            poll_interval_minutes = 20
            ignore_shorts = true
            queue_latest_on_start = true
            recent_per_channel = 3

            [[autopilot.channels]]
            name = "MrBeast"
            channel_id = "UCX6OQ3DkcsbYNE6H8uQQuVA"

            [[autopilot.channels]]
            name = "CarryMinati"
            channel_id = "UCj22tfcQrWG7EMEKS0qLeEg"
            """,
            encoding="utf-8",
        )

        with patch.dict("os.environ", {"CHANNEL_CONFIG_FILE": str(toml_path)}):
            settings = Settings.from_env(env_file=None)
            self.assertTrue(settings.autopilot_enabled)
            self.assertEqual(settings.autopilot_poll_minutes, 20)
            self.assertTrue(settings.autopilot_ignore_shorts)
            self.assertTrue(settings.autopilot_queue_latest_on_start)
            self.assertEqual(settings.autopilot_recent_per_channel, 3)
            self.assertEqual(len(settings.autopilot_channels), 2)
            self.assertEqual(settings.autopilot_channels[0]["name"], "MrBeast")
            self.assertEqual(settings.autopilot_channels[0]["channel_id"], "UCX6OQ3DkcsbYNE6H8uQQuVA")


if __name__ == "__main__":
    unittest.main()
