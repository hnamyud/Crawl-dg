from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


ProgressCallback = Callable[[str], None]
CaptureOne = Callable[[str, Path], None]


@dataclass(frozen=True)
class ScreenshotJob:
    notice_kind: str
    notice_id: str
    asset_index: int
    url: str
    sequence: int = 0


def screenshot_file_name(job: ScreenshotJob) -> str:
    notice_kind = _safe_file_part(job.notice_kind)
    notice_id = _safe_file_part(job.notice_id)
    return f"{job.sequence:06d}_{notice_kind}_{notice_id}_{job.asset_index:03d}.png"


def capture_screenshot_jobs(
    jobs: Iterable[ScreenshotJob],
    output_dir: Path,
    progress: ProgressCallback | None = None,
    capture_one: CaptureOne | None = None,
) -> None:
    job_list = list(jobs)
    if not job_list:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    total = len(job_list)
    if capture_one is not None:
        _capture_jobs(job_list, output_dir, progress, capture_one, total)
        return
    try:
        with _PlaywrightScreenshotter() as playwright_capture:
            _capture_jobs(job_list, output_dir, progress, playwright_capture, total)
    except Exception as exc:
        _emit(progress, f"Không khởi tạo được chụp ảnh: {exc}")


def _capture_jobs(
    jobs: list[ScreenshotJob],
    output_dir: Path,
    progress: ProgressCallback | None,
    capture_one: CaptureOne,
    total: int,
) -> None:
    for index, job in enumerate(jobs, start=1):
        file_name = screenshot_file_name(job)
        if not job.url.strip():
            _emit(progress, f"Bỏ qua chụp ảnh {file_name}: thiếu URL")
            _emit(progress, f"Đã chụp {index}/{total} ảnh...")
            continue
        try:
            capture_one(job.url, output_dir / file_name)
        except Exception as exc:
            _emit(progress, f"Không chụp được {file_name}: {exc}")
        _emit(progress, f"Đã chụp {index}/{total} ảnh...")


class _PlaywrightScreenshotter:
    def __enter__(self) -> CaptureOne:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError("Thiếu thư viện playwright. Hãy cài requirements và browser Chromium.") from exc
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)
        self._page = self._browser.new_page(viewport={"width": 1365, "height": 900})
        return self.capture

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if hasattr(self, "_browser"):
            self._browser.close()
        if hasattr(self, "_playwright"):
            self._playwright.stop()

    def capture(self, url: str, path: Path) -> None:
        self._page.goto(url, wait_until="networkidle", timeout=30000)
        self._page.screenshot(path=str(path), full_page=True)


def _safe_file_part(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in str(value).strip())
    return cleaned or "unknown"


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)
