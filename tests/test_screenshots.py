from pathlib import Path

from dgts_crawler.screenshots import ScreenshotJob, capture_screenshot_jobs, screenshot_file_name


def test_screenshot_file_name_uses_notice_kind_id_and_asset_index():
    job = ScreenshotJob("auction", "105862", 1, "https://example.test/detail.html", sequence=7)

    assert screenshot_file_name(job) == "000007_auction_105862_001.png"


def test_capture_screenshot_jobs_continues_after_one_job_fails(tmp_path):
    calls = []
    messages = []
    jobs = [
        ScreenshotJob("auction", "1", 1, "https://example.test/1.html", sequence=1),
        ScreenshotJob("auction", "2", 1, "https://example.test/2.html", sequence=2),
    ]

    def capture_one(url: str, path: Path) -> None:
        calls.append((url, path.name))
        if url.endswith("/1.html"):
            raise RuntimeError("boom")

    capture_screenshot_jobs(jobs, tmp_path, progress=messages.append, capture_one=capture_one)

    assert calls == [
        ("https://example.test/1.html", "000001_auction_1_001.png"),
        ("https://example.test/2.html", "000002_auction_2_001.png"),
    ]
    assert any("Không chụp được 000001_auction_1_001.png" in message for message in messages)
    assert messages[-1] == "Đã chụp 2/2 ảnh..."
