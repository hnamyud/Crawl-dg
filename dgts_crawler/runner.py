from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Literal, TypeVar

from .client import DGTSCrawlerClient
from .excel_writer import AuctionRow, write_select_org_result_workbook, write_select_org_workbook, write_workbook
from .history_store import HistorySnapshot, HistoryStore
from .screenshots import ScreenshotJob, capture_screenshot_jobs
from .transform import extract_province, normalize_notice, normalize_select_org_notices, normalize_select_org_result_notices
from .utils import VIETNAM_TZ


ProgressCallback = Callable[[str], None]
StopCallback = Callable[[], bool]
T = TypeVar("T")
R = TypeVar("R")


@dataclass(frozen=True)
class AuctionFilters:
    selected_organization_id: str = ""
    full_name: str = ""
    start_date: str = ""
    end_date: str = ""
    start_publish_date: str = ""
    end_publish_date: str = ""
    province_id: str = ""
    district_id: str = ""
    from_first_price: str = ""
    to_first_price: str = ""
    property_type_id: str = ""
    type_order: str = "2"
    asset_name: str = ""
    search_simple: str = ""


@dataclass(frozen=True)
class SelectOrgFilters:
    owner_fullname: str = ""
    start_date: str = ""
    end_date: str = ""
    start_publish_date: str = ""
    end_publish_date: str = ""
    province_id: str = ""
    district_id: str = ""
    property_type_id: str = ""
    notice_sub: str = ""


@dataclass(frozen=True)
class SelectOrgResultFilters:
    owner_fullname: str = ""
    org_id: str = ""
    publish_start_date: str = ""
    publish_end_date: str = ""
    province_id: str = ""
    district_id: str = ""
    property_type_id: str = ""
    notice_sub: str = ""


@dataclass(frozen=True)
class CrawlerConfig:
    from_date: str = ""
    to_date: str = ""
    max_pages: int = 10
    page_size: int = 10
    detail_workers: int = 1
    crawl_all: bool = False
    notice_kind: Literal["auction", "select-org", "select-org-result"] = "auction"
    output_path: Path = Path("DGTS_Output.xlsx")
    history_db_path: Path = Path("outputs") / "dgts_history.sqlite"
    enable_history: bool = True
    enable_screenshots: bool = False
    screenshot_dir: Path = Path("outputs") / "screenshots"
    screenshot_started_at: datetime | None = None
    # Kept opt-in for callers embedding the library. The UI and CLI enable it by default.
    timestamp_output: bool = False
    run_started_at: datetime | None = None
    auction_filters: AuctionFilters = AuctionFilters()
    select_org_filters: SelectOrgFilters = SelectOrgFilters()
    select_org_result_filters: SelectOrgResultFilters = SelectOrgResultFilters()


def run_crawl(
    config: CrawlerConfig,
    progress: ProgressCallback | None = None,
    should_stop: StopCallback | None = None,
    client: DGTSCrawlerClient | None = None,
) -> tuple[Path, int]:
    errors = validate_config(config)
    if errors:
        raise ValueError("\n".join(errors))

    run_started_at = config.run_started_at or datetime.now(VIETNAM_TZ)
    config = replace(
        config,
        run_started_at=run_started_at,
        screenshot_started_at=config.screenshot_started_at or run_started_at,
    )
    start_date, end_date = resolve_dates(config)
    _emit(progress, f"Khoảng ngày: {start_date or '(tất cả)'} -> {end_date or '(tất cả)'}")
    _emit(progress, f"Tên file gốc: {config.output_path}")

    client = client or DGTSCrawlerClient()
    if config.notice_kind == "select-org-result":
        return _run_select_org_result_crawl(config, client, start_date, end_date, progress, should_stop)
    if config.notice_kind == "select-org":
        return _run_select_org_crawl(config, client, start_date, end_date, progress, should_stop)
    return _run_auction_crawl(config, client, start_date, end_date, progress, should_stop)


def _run_auction_crawl(
    config: CrawlerConfig,
    client: DGTSCrawlerClient,
    start_date: str,
    end_date: str,
    progress: ProgressCallback | None,
    should_stop: StopCallback | None,
) -> tuple[Path, int]:
    rows: list[AuctionRow] = []
    snapshots: list[HistorySnapshot] = []
    screenshot_jobs: list[ScreenshotJob] = []
    crawl_completed = True
    checked_count = 0
    notices = client.iter_notices(
        start_date=start_date,
        end_date=end_date,
        page_size=config.page_size,
        max_pages=None if config.crawl_all else config.max_pages,
        filters=_auction_filters_for_run(config, start_date, end_date),
    )

    def fetch_notice(notice: dict) -> tuple[list[AuctionRow], HistorySnapshot, list[ScreenshotJob]]:
        detail = client.auction_detail(notice) if hasattr(client, "auction_detail") else client.property_detail(notice.get("id"))
        normalized_rows = normalize_notice(notice, detail)
        auction_rows = [AuctionRow(row.sheet_name, row.values) for row in normalized_rows]
        return (
            auction_rows,
            _auction_snapshot(notice, detail, normalized_rows[0]),
            _screenshot_jobs("auction", notice.get("id"), auction_rows),
        )

    for normalized_rows, snapshot, notice_screenshot_jobs in _map_details(notices, fetch_notice, config.detail_workers, should_stop):
        rows.extend(normalized_rows)
        snapshots.append(snapshot)
        screenshot_jobs.extend(notice_screenshot_jobs)
        checked_count += 1
        _emit_checked_progress(progress, checked_count)
        if rows and len(rows) % 10 == 0:
            _emit(progress, f"Đã xử lý {len(rows)} tin...")
        if should_stop and should_stop():
            _emit(progress, "Đã nhận yêu cầu dừng. Đang xuất file với dữ liệu hiện có...")
            crawl_completed = False
            break

    _capture_config_screenshots(config, screenshot_jobs, progress)
    _emit(progress, "Đang ghi file Excel...")
    output_path = _resolve_snapshot_output_path(config, partial=not crawl_completed or not config.crawl_all)
    _emit(progress, f"Snapshot Excel: {output_path}")
    output = write_workbook(output_path, rows)
    _record_history(
        config,
        "auction",
        start_date,
        end_date,
        snapshots,
        crawl_completed,
        progress,
        _missing_exists_validator("auction", client, progress),
        output,
        scope_complete=crawl_completed and config.crawl_all,
    )
    _emit(progress, _format_completion_message(checked_count, len(rows), output))
    return output, len(rows)


def _run_select_org_crawl(
    config: CrawlerConfig,
    client: DGTSCrawlerClient,
    start_date: str,
    end_date: str,
    progress: ProgressCallback | None,
    should_stop: StopCallback | None,
) -> tuple[Path, int]:
    rows: list[AuctionRow] = []
    snapshots: list[HistorySnapshot] = []
    screenshot_jobs: list[ScreenshotJob] = []
    crawl_completed = True
    checked_count = 0
    notices = client.iter_select_org_notices(
        start_date=start_date,
        end_date=end_date,
        page_size=config.page_size,
        max_pages=None if config.crawl_all else config.max_pages,
        filters=_select_org_filters_for_run(config, start_date, end_date),
    )

    def fetch_notice(notice: dict) -> tuple[list[AuctionRow], HistorySnapshot | None, list[ScreenshotJob]]:
        notice_id = notice.get("id")
        detail = client.select_org_detail(notice_id)
        property_rows = client.select_org_property_info(notice_id)
        normalized_rows = normalize_select_org_notices(notice, detail, property_rows)
        if not normalized_rows or not _date_in_range(normalized_rows[0].values[1], start_date, end_date):
            return [], None, []
        select_org_rows = [AuctionRow(normalized.sheet_name, normalized.values) for normalized in normalized_rows]
        return (
            select_org_rows,
            _select_org_snapshot(notice, detail, property_rows, normalized_rows),
            _screenshot_jobs("select-org", notice_id, select_org_rows),
        )

    for normalized_rows, snapshot, notice_screenshot_jobs in _map_details(notices, fetch_notice, config.detail_workers, should_stop):
        checked_count += 1
        _emit_checked_progress(progress, checked_count)
        rows.extend(normalized_rows)
        screenshot_jobs.extend(notice_screenshot_jobs)
        if snapshot:
            snapshots.append(snapshot)
        if rows and len(rows) % 10 == 0:
            _emit(progress, f"Đã xử lý {len(rows)} tin...")
        if should_stop and should_stop():
            _emit(progress, "Đã nhận yêu cầu dừng. Đang xuất file với dữ liệu hiện có...")
            crawl_completed = False
            break

    _capture_config_screenshots(config, screenshot_jobs, progress)
    _emit(progress, "Đang ghi file Excel...")
    output_path = _resolve_snapshot_output_path(config, partial=not crawl_completed or not config.crawl_all)
    _emit(progress, f"Snapshot Excel: {output_path}")
    output = write_select_org_workbook(output_path, rows)
    _record_history(
        config,
        "select-org",
        start_date,
        end_date,
        snapshots,
        crawl_completed,
        progress,
        _missing_exists_validator("select-org", client, progress),
        output,
        scope_complete=crawl_completed and config.crawl_all,
    )
    _emit(progress, _format_completion_message(checked_count, len(rows), output))
    return output, len(rows)


def _run_select_org_result_crawl(
    config: CrawlerConfig,
    client: DGTSCrawlerClient,
    start_date: str,
    end_date: str,
    progress: ProgressCallback | None,
    should_stop: StopCallback | None,
) -> tuple[Path, int]:
    rows: list[AuctionRow] = []
    snapshots: list[HistorySnapshot] = []
    screenshot_jobs: list[ScreenshotJob] = []
    crawl_completed = True
    checked_count = 0
    notices = client.iter_select_org_result_notices(
        start_date=start_date,
        end_date=end_date,
        page_size=config.page_size,
        max_pages=None if config.crawl_all else config.max_pages,
        filters=_select_org_result_filters_for_run(config),
    )

    def fetch_notice(notice: dict) -> tuple[list[AuctionRow], HistorySnapshot | None, list[ScreenshotJob]]:
        result_id = notice.get("id")
        owner = client.select_org_result_owner(result_id)
        history = client.select_org_result_history(result_id)
        info = client.select_org_result_info(result_id)
        normalized_rows = normalize_select_org_result_notices(notice, owner, history, info)
        if not normalized_rows or not _date_in_range(normalized_rows[0].values[1], start_date, end_date):
            return [], None, []
        result_rows = [AuctionRow(normalized.sheet_name, normalized.values) for normalized in normalized_rows]
        return (
            result_rows,
            _select_org_result_snapshot(notice, owner, history, info, normalized_rows),
            _screenshot_jobs("select-org-result", result_id, result_rows),
        )

    for normalized_rows, snapshot, notice_screenshot_jobs in _map_details(notices, fetch_notice, config.detail_workers, should_stop):
        checked_count += 1
        _emit_checked_progress(progress, checked_count)
        rows.extend(normalized_rows)
        screenshot_jobs.extend(notice_screenshot_jobs)
        if snapshot:
            snapshots.append(snapshot)
        if rows and len(rows) % 10 == 0:
            _emit(progress, f"Đã xử lý {len(rows)} tin...")
        if should_stop and should_stop():
            _emit(progress, "Đã nhận yêu cầu dừng. Đang xuất file với dữ liệu hiện có...")
            crawl_completed = False
            break

    _capture_config_screenshots(config, screenshot_jobs, progress)
    _emit(progress, "Đang ghi file Excel...")
    output_path = _resolve_snapshot_output_path(config, partial=not crawl_completed or not config.crawl_all)
    _emit(progress, f"Snapshot Excel: {output_path}")
    output = write_select_org_result_workbook(output_path, rows)
    _record_history(
        config,
        "select-org-result",
        start_date,
        end_date,
        snapshots,
        crawl_completed,
        progress,
        _missing_exists_validator("select-org-result", client, progress),
        output,
        scope_complete=crawl_completed and config.crawl_all,
    )
    _emit(progress, _format_completion_message(checked_count, len(rows), output))
    return output, len(rows)


def validate_config(config: CrawlerConfig) -> list[str]:
    errors: list[str] = []
    if config.max_pages <= 0:
        errors.append("Số trang tối đa phải lớn hơn 0.")
    if config.page_size <= 0:
        errors.append("Số bản ghi mỗi trang phải lớn hơn 0.")
    if config.detail_workers <= 0:
        errors.append("Số luồng tải chi tiết phải lớn hơn 0.")
    if config.notice_kind not in {"auction", "select-org", "select-org-result"}:
        errors.append("Loại thông báo không hợp lệ.")
    try:
        format_date_arg(config.from_date)
    except ValueError:
        errors.append("Ngày bắt đầu phải có dạng dd/MM/yyyy hoặc yyyy-mm-dd.")
    try:
        format_date_arg(config.to_date)
    except ValueError:
        errors.append("Ngày kết thúc phải có dạng dd/MM/yyyy hoặc yyyy-mm-dd.")
    return errors


def _capture_config_screenshots(
    config: CrawlerConfig,
    screenshot_jobs: list[ScreenshotJob],
    progress: ProgressCallback | None,
) -> None:
    if not config.enable_screenshots:
        return
    output_dir = config.screenshot_dir / _screenshot_run_folder_name(config.notice_kind, config.screenshot_started_at)
    _emit(progress, f"Thư mục lưu ảnh: {output_dir}")
    if not screenshot_jobs:
        _emit(progress, "Không có bài nào để chụp ảnh.")
        return
    _emit(progress, f"Đang chụp ảnh {len(screenshot_jobs)} bài...")
    capture_screenshot_jobs(_numbered_screenshot_jobs(screenshot_jobs), output_dir, progress=progress)


def _numbered_screenshot_jobs(jobs: list[ScreenshotJob]) -> list[ScreenshotJob]:
    return [
        ScreenshotJob(
            notice_kind=job.notice_kind,
            notice_id=job.notice_id,
            asset_index=job.asset_index,
            url=job.url,
            sequence=index,
        )
        for index, job in enumerate(jobs, start=1)
    ]


def _screenshot_run_folder_name(notice_kind: str, started_at: datetime | None = None) -> str:
    timestamp = (started_at or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    return f"{notice_kind}_{timestamp}"


def _resolve_snapshot_output_path(config: CrawlerConfig, partial: bool) -> Path:
    """Return a unique, immutable workbook path for this crawl run."""
    output = Path(config.output_path)
    if not config.timestamp_output:
        return output
    started_at = config.run_started_at or datetime.now(VIETNAM_TZ)
    suffix = output.suffix or ".xlsx"
    stem = output.stem if output.suffix else output.name
    timestamp = started_at.astimezone(VIETNAM_TZ).strftime("%Y%m%d_%H%M%S")
    partial_suffix = "_PARTIAL" if partial else ""
    candidate = output.with_name(f"{stem}_{timestamp}{partial_suffix}{suffix}")
    sequence = 2
    while candidate.exists():
        candidate = output.with_name(f"{stem}_{timestamp}{partial_suffix}_{sequence:02d}{suffix}")
        sequence += 1
    return candidate


def _screenshot_jobs(notice_kind: str, notice_id: Any, rows: list[AuctionRow]) -> list[ScreenshotJob]:
    url_index = _detail_url_index(notice_kind)
    jobs: list[ScreenshotJob] = []
    for asset_index, row in enumerate(rows, start=1):
        values = _padded(row.values, url_index + 1)
        url = str(values[url_index] or "").strip()
        jobs.append(
            ScreenshotJob(
                notice_kind=notice_kind,
                notice_id=str(notice_id or ""),
                asset_index=asset_index,
                url=url,
            )
        )
    return jobs


def _detail_url_index(notice_kind: str) -> int:
    if notice_kind == "auction":
        return 17
    if notice_kind == "select-org":
        return 12
    return 11


def resolve_dates(config: CrawlerConfig, today: datetime | None = None) -> tuple[str, str]:
    if config.crawl_all:
        return format_date_arg(config.from_date), format_date_arg(config.to_date)
    today = today or datetime.now()
    default_start = today - timedelta(days=7)
    return (
        format_date_arg(config.from_date) if config.from_date else default_start.strftime("%d/%m/%Y"),
        format_date_arg(config.to_date) if config.to_date else today.strftime("%d/%m/%Y"),
    )


def _select_org_result_filters_for_run(config: CrawlerConfig) -> SelectOrgResultFilters:
    filters = config.select_org_result_filters
    return SelectOrgResultFilters(
        owner_fullname=filters.owner_fullname,
        org_id=filters.org_id,
        publish_start_date=format_date_arg(filters.publish_start_date),
        publish_end_date=format_date_arg(filters.publish_end_date),
        province_id=filters.province_id,
        district_id=filters.district_id,
        property_type_id=filters.property_type_id,
        notice_sub=filters.notice_sub,
    )


def _select_org_filters_for_run(config: CrawlerConfig, start_date: str, end_date: str) -> SelectOrgFilters:
    filters = config.select_org_filters
    return SelectOrgFilters(
        owner_fullname=filters.owner_fullname,
        start_date=format_date_arg(filters.start_date) or start_date,
        end_date=format_date_arg(filters.end_date) or end_date,
        start_publish_date=format_date_arg(filters.start_publish_date),
        end_publish_date=format_date_arg(filters.end_publish_date),
        province_id=filters.province_id,
        district_id=filters.district_id,
        property_type_id=filters.property_type_id,
        notice_sub=filters.notice_sub,
    )


def _auction_filters_for_run(config: CrawlerConfig, start_date: str, end_date: str) -> AuctionFilters:
    filters = config.auction_filters
    return AuctionFilters(
        selected_organization_id=filters.selected_organization_id,
        full_name=filters.full_name,
        start_date=format_date_arg(filters.start_date),
        end_date=format_date_arg(filters.end_date),
        start_publish_date=format_date_arg(filters.start_publish_date) or start_date,
        end_publish_date=format_date_arg(filters.end_publish_date) or end_date,
        province_id=filters.province_id,
        district_id=filters.district_id,
        from_first_price=filters.from_first_price,
        to_first_price=filters.to_first_price,
        property_type_id=filters.property_type_id,
        type_order=filters.type_order or "2",
        asset_name=filters.asset_name,
        search_simple=filters.search_simple,
    )


def format_date_arg(value: str) -> str:
    if not value:
        return ""
    if "/" in value:
        datetime.strptime(value, "%d/%m/%Y")
        return value
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return parsed.strftime("%d/%m/%Y")


def _date_in_range(value: str, start_date: str, end_date: str) -> bool:
    if not value:
        return not start_date and not end_date
    parsed = datetime.strptime(value, "%d/%m/%Y")
    if start_date and parsed < datetime.strptime(start_date, "%d/%m/%Y"):
        return False
    if end_date and parsed > datetime.strptime(end_date, "%d/%m/%Y"):
        return False
    return True


def _emit(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)


def _emit_checked_progress(progress: ProgressCallback | None, checked_count: int) -> None:
    if checked_count == 1 or checked_count % 5 == 0:
        _emit(progress, f"Đã kiểm tra {checked_count} tin...")


def _format_completion_message(checked_count: int, exported_count: int, output: Path) -> str:
    return f"Hoàn tất: đã kiểm tra {checked_count} tin, xuất {exported_count} dòng -> {output}"


def _map_details(
    items: Iterable[T],
    worker: Callable[[T], R],
    max_workers: int,
    should_stop: StopCallback | None,
) -> Iterator[R]:
    if max_workers <= 1:
        for item in items:
            if should_stop and should_stop():
                break
            yield worker(item)
        return

    iterator = iter(items)
    executor = ThreadPoolExecutor(max_workers=max_workers)
    pending: dict[Future[R], None] = {}

    def submit_next() -> bool:
        if should_stop and should_stop():
            return False
        try:
            item = next(iterator)
        except StopIteration:
            return False
        pending[executor.submit(worker, item)] = None
        return True

    try:
        for _ in range(max_workers):
            if not submit_next():
                break
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                yield future.result()
                if should_stop and should_stop():
                    return
                submit_next()
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)


def _record_history(
    config: CrawlerConfig,
    notice_kind: str,
    start_date: str,
    end_date: str,
    snapshots: list[HistorySnapshot],
    detect_missing: bool,
    progress: ProgressCallback | None,
    missing_exists_validator: Callable[[HistorySnapshot], bool] | None = None,
    output_path: Path | None = None,
    scope_complete: bool = False,
) -> None:
    if not config.enable_history:
        return
    _emit(progress, "Đang cập nhật lịch sử thay đổi...")
    store = HistoryStore(config.history_db_path)
    record_args = {
        "notice_kind": notice_kind,
        "start_date": start_date,
        "end_date": end_date,
        "snapshots": snapshots,
        "detect_missing": detect_missing,
        "scope_complete": scope_complete,
        "output_path": str(output_path or config.output_path),
        "run_started_at": config.run_started_at,
        "missing_exists_validator": missing_exists_validator,
    }
    try:
        result = store.record_crawl(**record_args)
    except TypeError as exc:
        # Third-party integrations written against the pre-snapshot method can still run.
        if "unexpected keyword argument" not in str(exc):
            raise
        for key in ("scope_complete", "output_path", "run_started_at"):
            record_args.pop(key, None)
        result = store.record_crawl(**record_args)
    counts = result.event_counts
    missing_note = "" if scope_complete else " (chỉ kiểm tra trạng thái biến mất khi crawl toàn bộ dữ liệu)"
    _emit(
        progress,
        "Lịch sử: "
        f"NEW={counts.get('NEW', 0)}, "
        f"CHANGED={counts.get('CHANGED', 0)}, "
        f"DELISTED={counts.get('DELISTED', 0)}, "
        f"REMOVED={counts.get('REMOVED', 0)}, "
        f"REAPPEARED={counts.get('REAPPEARED', 0)}, "
        f"SUSPECT_REPOST={counts.get('SUSPECT_REPOST', 0)}, "
        f"SAME_ASSET_NAME={counts.get('SAME_ASSET_NAME', 0)}"
        f"{missing_note}",
    )


def _missing_exists_validator(
    notice_kind: str,
    client: DGTSCrawlerClient,
    progress: ProgressCallback | None,
) -> Callable[[HistorySnapshot], str]:
    def validator(snapshot: HistorySnapshot) -> str:
        try:
            return "EXISTS" if _missing_detail_exists(notice_kind, client, snapshot.notice_id) else "NOT_FOUND"
        except Exception as exc:
            _emit(
                progress,
                f"Bỏ qua MISSING cho {snapshot.notice_kind} {snapshot.notice_id}: "
                f"không kiểm tra được detail ({exc})",
            )
            return "ACCESS_ERROR"

    return validator


def _missing_detail_exists(notice_kind: str, client: DGTSCrawlerClient, notice_id: str) -> bool:
    if notice_kind == "auction":
        if hasattr(client, "_get_json"):
            payload = client._get_json("/portal/propertyInfo", {"auctionInfoId": notice_id})
            return _payload_has_data(payload.get("items") if isinstance(payload, dict) else payload)
        return _payload_has_data(client.property_detail(notice_id))
    if notice_kind == "select-org":
        detail = client.select_org_detail(notice_id)
        property_rows = client.select_org_property_info(notice_id)
        return _payload_has_data(detail) or _payload_has_data(property_rows)
    if notice_kind == "select-org-result":
        info = client.select_org_result_info(notice_id)
        history = client.select_org_result_history(notice_id)
        owner = client.select_org_result_owner(notice_id)
        return _payload_has_data(info) or _payload_has_data(history) or _payload_has_data(owner)
    return True


def _payload_has_data(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(_payload_has_data(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_payload_has_data(item) for item in value)
    return True


def _auction_snapshot(notice: dict, detail: dict, normalized: AuctionRow) -> HistorySnapshot:
    values = _padded(normalized.values, 18)
    property_place = str(values[8] or "")
    tracked_fields = {
        "publish_date": values[1],
        "publish_time1": notice.get("publishTime1"),
        "publish_time2": notice.get("publishTime2"),
        "publish_round": 2 if notice.get("publishTime2") else 1,
        "notice_code": f"TS_{notice.get('id') or ''}",
        "asset_name": values[6],
        "province": extract_province(property_place),
        "owner_name": values[3],
        "start_price": values[9],
        "deposit": values[10],
        "deadline": values[13],
        "detail_url": values[17],
        "property_place": property_place,
        "group": normalized.sheet_name,
    }
    return HistorySnapshot(
        notice_kind="auction",
        notice_id=str(notice.get("id") or ""),
        publish_date=str(values[1] or ""),
        detail_url=str(values[17] or ""),
        tracked_fields=tracked_fields,
        raw_payload={"notice": notice, "detail": detail},
        publish_time1=_int_or_none(notice.get("publishTime1")),
        publish_time2=_int_or_none(notice.get("publishTime2")),
    )


def _select_org_snapshot(
    notice: dict,
    detail: dict,
    property_rows: list[dict],
    normalized_rows: list[AuctionRow],
) -> HistorySnapshot:
    first = _padded(normalized_rows[0].values, 13)
    tracked_fields = {
        "publish_date": first[1],
        "owner_name": first[3],
        "owner_address": first[4],
        "receive_start": first[8],
        "receive_end": first[9],
        "receive_address": first[10],
        "contact_info": first[11],
        "detail_url": first[12],
        "properties": [
            {
                "asset_name": row_values[2],
                "amount": row_values[5],
                "quality": row_values[6],
                "start_price": row_values[7],
            }
            for row_values in (_padded(row.values, 13) for row in normalized_rows)
        ],
    }
    return HistorySnapshot(
        notice_kind="select-org",
        notice_id=str(notice.get("id") or ""),
        publish_date=str(first[1] or ""),
        detail_url=str(first[12] or ""),
        tracked_fields=tracked_fields,
        raw_payload={"notice": notice, "detail": detail, "property_rows": property_rows},
    )


def _select_org_result_snapshot(
    notice: dict,
    owner: dict,
    history: dict,
    info: dict,
    normalized_rows: list[AuctionRow],
) -> HistorySnapshot:
    first = _padded(normalized_rows[0].values, 12)
    tracked_fields = {
        "publish_date": first[1],
        "owner_name": first[3],
        "owner_address": first[4],
        "selected_org": first[8],
        "selected_org_address": first[9],
        "contact_info": first[10],
        "detail_url": first[11],
        "properties": [
            {
                "asset_name": row_values[2],
                "amount": row_values[5],
                "quality": row_values[6],
                "start_price": row_values[7],
            }
            for row_values in (_padded(row.values, 12) for row in normalized_rows)
        ],
    }
    return HistorySnapshot(
        notice_kind="select-org-result",
        notice_id=str(notice.get("id") or ""),
        publish_date=str(first[1] or ""),
        detail_url=str(first[11] or ""),
        tracked_fields=tracked_fields,
        raw_payload={"notice": notice, "owner": owner, "history": history, "info": info},
    )


def _padded(values: list, length: int) -> list:
    padded = list(values)
    while len(padded) < length:
        padded.append("")
    return padded


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
