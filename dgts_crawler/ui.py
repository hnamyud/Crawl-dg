from __future__ import annotations

import queue
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from tkcalendar import DateEntry

from .client import DGTSCrawlerClient
from .history_report import write_history_report
from .history_store import HistoryEventRow, HistoryStore
from .runner import AuctionFilters, CrawlerConfig, SelectOrgFilters, SelectOrgResultFilters, run_crawl, validate_config


@dataclass(frozen=True)
class TabConfig:
    notice_kind: str
    output_default: Path
    tab_label: str
    header: str


TAB_CONFIGS = [
    TabConfig(
        notice_kind="auction",
        output_default=Path("outputs") / "dgts_auction_notices.xlsx",
        tab_label="Tab 1",
        header="Thông báo công khai việc đấu giá",
    ),
    TabConfig(
        notice_kind="select-org",
        output_default=Path("outputs") / "dgts_select_org_notices.xlsx",
        tab_label="Tab 2",
        header="Danh sách thông báo lựa chọn tổ chức hành nghề đấu giá",
    ),
    TabConfig(
        notice_kind="select-org-result",
        output_default=Path("outputs") / "dgts_select_org_result_notices.xlsx",
        tab_label="Tab 3",
        header="Danh sách thông báo kết quả lựa chọn tổ chức hành nghề đấu giá",
    ),
]
HISTORY_TAB_LABEL = "Tab 4"
NOTICE_KIND_OPTIONS = {
    "Tất cả": "",
    "Tab 1 - Đấu giá": "auction",
    "Tab 2 - Lựa chọn tổ chức": "select-org",
    "Tab 3 - Kết quả lựa chọn": "select-org-result",
}
EVENT_OPTIONS = {
    "Tất cả": "",
    "NEW": "NEW",
    "CHANGED": "CHANGED",
    "MISSING": "MISSING",
    "REAPPEARED": "REAPPEARED",
    "SUSPECT_REPOST": "SUSPECT_REPOST",
    "SAME_ASSET_NAME": "SAME_ASSET_NAME",
}
DEFAULT_UI_PAGE_SIZE = "100"
DEFAULT_DETAIL_WORKERS = "5"
HISTORY_PAGE_SIZE = 500


def format_done_message(output: Path) -> str:
    return f"Hoàn tất. File: {output}"


def format_history_page_status(page_index: int, row_count: int) -> str:
    return f"Trang {page_index + 1} - {row_count} dòng"


def format_history_table_value(row: HistoryEventRow, column: str) -> str:
    if column == "changed_details" and row.event_type == "SUSPECT_REPOST":
        return _format_suspect_repost_summary(row.changed_details)
    if column == "changed_fields" and row.event_type == "SUSPECT_REPOST":
        return "notice_id, publish_date"
    value = str(getattr(row, column, ""))
    return _single_line(value)


def _format_suspect_repost_summary(details: str) -> str:
    values = {}
    for line in details.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    old_id = values.get("Tin cũ", "")
    new_id = values.get("Tin mới", "")
    old_date = values.get("Ngày đăng cũ", "")
    new_date = values.get("Ngày đăng mới", "")
    if old_id and new_id:
        date_part = f" ({old_date} -> {new_date})" if old_date or new_date else ""
        return f"Cũ {old_id} -> Mới {new_id}{date_part}"
    return _single_line(details)


def _single_line(value: str, max_length: int = 240) -> str:
    normalized = " | ".join(part.strip() for part in value.splitlines() if part.strip())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def _option_map(items: object, label_field: str) -> dict[str, str]:
    options = {"Tất cả": ""}
    if not isinstance(items, list):
        return options
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(item.get(label_field) or "").strip()
        value = str(item.get("id") or "").strip()
        if label and value:
            options[label] = value
    return options


class RunCoordinator:
    def __init__(self) -> None:
        self.active_label = ""

    def try_start(self, label: str) -> bool:
        if self.active_label:
            return False
        self.active_label = label
        return True

    def finish(self, label: str) -> None:
        if self.active_label == label:
            self.active_label = ""


class CrawlerTab(ttk.Frame):
    def __init__(
        self,
        parent: ttk.Notebook,
        notice_kind: str,
        output_default: Path,
        header: str,
        run_label: str,
        coordinator: RunCoordinator,
    ) -> None:
        super().__init__(parent, padding=16)
        self.notice_kind = notice_kind
        self.header = header
        self.run_label = run_label
        self.coordinator = coordinator

        today = datetime.now()
        self.from_date = tk.StringVar(value=(today - timedelta(days=7)).strftime("%d/%m/%Y"))
        self.to_date = tk.StringVar(value=today.strftime("%d/%m/%Y"))
        # --- Auction (Tab 1) filter vars ---
        self.auction_start_date = tk.StringVar(value="")
        self.auction_end_date = tk.StringVar(value="")
        self.auction_start_publish_date = tk.StringVar(value=self.from_date.get())
        self.auction_end_publish_date = tk.StringVar(value=self.to_date.get())
        self.auction_full_name = tk.StringVar(value="")
        self.auction_from_price = tk.StringVar(value="")
        self.auction_to_price = tk.StringVar(value="")
        self.auction_type_order = tk.StringVar(value="Ngày công khai việc đấu giá")
        self.auction_selected_org = tk.StringVar(value="Tất cả")
        self.auction_province = tk.StringVar(value="Tất cả")
        self.auction_district = tk.StringVar(value="Tất cả")
        self.auction_property_type = tk.StringVar(value="Tất cả")
        self._auction_org_options: dict[str, str] = {"Tất cả": ""}
        self._auction_province_options: dict[str, str] = {"Tất cả": ""}
        self._auction_district_options: dict[str, str] = {"Tất cả": ""}
        self._auction_property_type_options: dict[str, str] = {"Tất cả": ""}
        self._auction_option_events: queue.Queue[tuple[str, object]] = queue.Queue()
        # --- Select-Org (Tab 2) filter vars ---
        self.select_org_owner_fullname = tk.StringVar(value="")
        self.select_org_start_date = tk.StringVar(value="")
        self.select_org_end_date = tk.StringVar(value="")
        self.select_org_province = tk.StringVar(value="Tất cả")
        self.select_org_district = tk.StringVar(value="Tất cả")
        self.select_org_property_type = tk.StringVar(value="Tất cả")
        self._select_org_province_options: dict[str, str] = {"Tất cả": ""}
        self._select_org_district_options: dict[str, str] = {"Tất cả": ""}
        self._select_org_property_type_options: dict[str, str] = {"Tất cả": ""}
        self._select_org_option_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.select_org_start_date_picker: DateEntry | None = None
        self.select_org_end_date_picker: DateEntry | None = None
        self.select_org_province_combo: ttk.Combobox | None = None
        self.select_org_district_combo: ttk.Combobox | None = None
        self.select_org_property_type_combo: ttk.Combobox | None = None
        # --- Select-Org-Result (Tab 3) filter vars ---
        self.result_owner_fullname = tk.StringVar(value="")
        self.result_org = tk.StringVar(value="Tất cả")
        self.result_publish_start_date = tk.StringVar(value="")
        self.result_publish_end_date = tk.StringVar(value="")
        self.result_province = tk.StringVar(value="Tất cả")
        self.result_district = tk.StringVar(value="Tất cả")
        self.result_property_type = tk.StringVar(value="Tất cả")
        self._result_org_options: dict[str, str] = {"Tất cả": ""}
        self._result_province_options: dict[str, str] = {"Tất cả": ""}
        self._result_district_options: dict[str, str] = {"Tất cả": ""}
        self._result_property_type_options: dict[str, str] = {"Tất cả": ""}
        self._result_option_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.result_publish_start_picker: DateEntry | None = None
        self.result_publish_end_picker: DateEntry | None = None
        self.result_org_combo: ttk.Combobox | None = None
        self.result_province_combo: ttk.Combobox | None = None
        self.result_district_combo: ttk.Combobox | None = None
        self.result_property_type_combo: ttk.Combobox | None = None
        # --- Shared ---
        self.max_pages = tk.StringVar(value="10")
        self.page_size = tk.StringVar(value=DEFAULT_UI_PAGE_SIZE)
        self.detail_workers = tk.StringVar(value=DEFAULT_DETAIL_WORKERS)
        self.crawl_all = tk.BooleanVar(value=False)
        self.output_path = tk.StringVar(value=str(output_default))
        self.history_db_path = tk.StringVar(value=str(Path("outputs") / "dgts_history.sqlite"))
        self.enable_history = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Sẵn sàng")
        self.events: queue.Queue[tuple[int, str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.run_id = 0
        self.from_date_picker: DateEntry | None = None
        self.to_date_picker: DateEntry | None = None
        self.auction_start_date_picker: DateEntry | None = None
        self.auction_end_date_picker: DateEntry | None = None
        self.auction_start_publish_date_picker: DateEntry | None = None
        self.auction_end_publish_date_picker: DateEntry | None = None
        self.auction_org_combo: ttk.Combobox | None = None
        self.auction_province_combo: ttk.Combobox | None = None
        self.auction_district_combo: ttk.Combobox | None = None
        self.auction_property_type_combo: ttk.Combobox | None = None

        self._log_batch: list[str] = []
        self._build_layout()
        if self.notice_kind == "auction":
            self._load_initial_auction_options()
        elif self.notice_kind == "select-org":
            self._load_initial_select_org_options()
        elif self.notice_kind == "select-org-result":
            self._load_initial_result_options()
        self.after(250, self._drain_events)

    def _build_layout(self) -> None:
        if self.notice_kind == "auction":
            self._build_auction_layout()
        elif self.notice_kind == "select-org":
            self._build_select_org_layout()
        elif self.notice_kind == "select-org-result":
            self._build_select_org_result_layout()
        else:
            self._build_simple_layout()

    def _build_simple_layout(self) -> None:
        """Simple date-range-only layout used by Tab 3 (select-org-result)."""
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=self.header, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        self.from_date_picker = self._date_entry(1, "Từ ngày", self.from_date)
        self.to_date_picker = self._date_entry(2, "Đến ngày", self.to_date)
        self._preset_buttons(3)
        self._entry(4, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(5, "Số bản ghi mỗi trang", self.page_size, "Mặc định 100 bản ghi để crawl nhanh hơn")
        self._entry(6, "Số luồng tải chi tiết", self.detail_workers, "Mặc định 5 luồng")
        ttk.Checkbutton(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc ngày", variable=self.crawl_all).grid(
            row=7, column=1, sticky="w", pady=8
        )
        self._path_entry(8, "File lưu kết quả", self.output_path, self._browse_output)
        self._path_entry(9, "File DB lịch sử", self.history_db_path, self._browse_history_db)
        ttk.Checkbutton(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=10, column=1, sticky="w", pady=8)
        actions = ttk.Frame(self)
        actions.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(16, 8))
        actions.columnconfigure(2, weight=1)
        self.run_button = ttk.Button(actions, text="Bắt đầu crawl", command=self._start_crawl)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(actions, text="Dừng và xuất file", command=self._stop_crawl, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(actions, text="Clear bộ lọc", command=self._clear_filters).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=3, sticky="e")
        ttk.Label(self, text="Log").grid(row=12, column=0, sticky="nw", pady=(8, 4))
        self.log = tk.Text(self, height=14, wrap="word", state="disabled")
        self.log.grid(row=13, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(13, weight=1)

    def _build_select_org_result_layout(self) -> None:
        """Rich filter layout for Tab 3 (select-org-result), matching the DGTS website."""
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=self.header, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        self._entry_field(1, 0, "Người có tài sản", self.result_owner_fullname, "Tên người có tài sản")
        self.result_org_combo = self._combo_field(1, 1, "Tên Tổ chức", self.result_org)
        self.result_province_combo = self._combo_field(2, 0, "Tỉnh/Thành phố", self.result_province)
        self.result_province_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_result_province_selected())
        self.result_district_combo = self._combo_field(2, 1, "Quận/Huyện", self.result_district)
        self.result_district_combo.configure(state="disabled")
        self.result_property_type_combo = self._combo_field(3, 0, "Loại tài sản", self.result_property_type)
        self.result_publish_start_picker, self.result_publish_end_picker = self._date_range_field(
            3, 1, "Ngày công khai (từ → đến)", self.result_publish_start_date, self.result_publish_end_date
        )
        self._entry(4, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(5, "Số bản ghi mỗi trang", self.page_size, "Mặc định 100 bản ghi để crawl nhanh hơn")
        self._entry(6, "Số luồng tải chi tiết", self.detail_workers, "Mặc định 5 luồng")
        ttk.Checkbutton(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc", variable=self.crawl_all).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=8
        )
        self._path_entry(8, "File lưu kết quả", self.output_path, self._browse_output)
        self._path_entry(9, "File DB lịch sử", self.history_db_path, self._browse_history_db)
        ttk.Checkbutton(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=8)
        actions = ttk.Frame(self)
        actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(16, 8))
        actions.columnconfigure(2, weight=1)
        self.run_button = ttk.Button(actions, text="Bắt đầu crawl", command=self._start_crawl)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(actions, text="Dừng và xuất file", command=self._stop_crawl, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(actions, text="Clear bộ lọc", command=self._clear_filters).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=3, sticky="e")
        ttk.Label(self, text="Log").grid(row=12, column=0, sticky="nw", pady=(8, 4))
        self.log = tk.Text(self, height=14, wrap="word", state="disabled")
        self.log.grid(row=13, column=0, columnspan=2, sticky="nsew")
        self.rowconfigure(13, weight=1)

    def _build_select_org_layout(self) -> None:
        """Rich filter layout for Tab 2 (select-org), matching the DGTS website."""
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=self.header, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        self._entry_field(1, 0, "Người có tài sản", self.select_org_owner_fullname, "Tên người có tài sản")
        self.select_org_start_date_picker, self.select_org_end_date_picker = self._date_range_field(
            1, 1, "Thời gian nộp hồ sơ", self.select_org_start_date, self.select_org_end_date
        )
        self.select_org_province_combo = self._combo_field(2, 0, "Tỉnh/Thành phố", self.select_org_province)
        self.select_org_province_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_select_org_province_selected())
        self.select_org_district_combo = self._combo_field(2, 1, "Quận/Huyện", self.select_org_district)
        self.select_org_district_combo.configure(state="disabled")
        self.select_org_property_type_combo = self._combo_field(3, 0, "Loại tài sản", self.select_org_property_type)
        self._entry(4, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(5, "Số bản ghi mỗi trang", self.page_size, "Mặc định 100 bản ghi để crawl nhanh hơn")
        self._entry(6, "Số luồng tải chi tiết", self.detail_workers, "Mặc định 5 luồng")
        ttk.Checkbutton(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc", variable=self.crawl_all).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=8
        )
        self._path_entry(8, "File lưu kết quả", self.output_path, self._browse_output)
        self._path_entry(9, "File DB lịch sử", self.history_db_path, self._browse_history_db)
        ttk.Checkbutton(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=10, column=0, columnspan=2, sticky="w", pady=8)
        actions = ttk.Frame(self)
        actions.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(16, 8))
        actions.columnconfigure(2, weight=1)
        self.run_button = ttk.Button(actions, text="Bắt đầu crawl", command=self._start_crawl)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(actions, text="Dừng và xuất file", command=self._stop_crawl, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(actions, text="Clear bộ lọc", command=self._clear_filters).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=3, sticky="e")
        ttk.Label(self, text="Log").grid(row=12, column=0, sticky="nw", pady=(8, 4))
        self.log = tk.Text(self, height=14, wrap="word", state="disabled")
        self.log.grid(row=13, column=0, columnspan=2, sticky="nsew")
        self.rowconfigure(13, weight=1)

    def _build_auction_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=self.header, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        self.auction_org_combo = self._combo_field(1, 0, "Tổ chức hành nghề đấu giá", self.auction_selected_org)
        self._entry_field(1, 1, "Người có tài sản", self.auction_full_name, "Họ và tên người có tài sản")
        self.auction_start_date_picker, self.auction_end_date_picker = self._date_range_field(
            2, 0, "Thời gian tổ chức cuộc đấu giá", self.auction_start_date, self.auction_end_date
        )
        self.auction_start_publish_date_picker, self.auction_end_publish_date_picker = self._date_range_field(
            2, 1, "Thời gian công khai việc đấu giá", self.auction_start_publish_date, self.auction_end_publish_date
        )
        self.auction_province_combo = self._combo_field(3, 0, "Tỉnh thành phố", self.auction_province)
        self.auction_province_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_auction_province_selected())
        self.auction_district_combo = self._combo_field(3, 1, "Quận/huyện", self.auction_district)
        self.auction_district_combo.configure(state="disabled")
        self._price_range_field(4, 0)
        self._combo_field(
            4,
            1,
            "Tiêu chí sắp xếp",
            self.auction_type_order,
            values=["Ngày công khai việc đấu giá", "Ngày tổ chức đấu giá"],
        )
        self.auction_property_type_combo = self._combo_field(5, 0, "Loại tài sản", self.auction_property_type)
        self._entry(6, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(7, "Số bản ghi mỗi trang", self.page_size, "Mặc định 100 bản ghi để crawl nhanh hơn")
        self._entry(8, "Số luồng tải chi tiết", self.detail_workers, "Mặc định 5 luồng")
        ttk.Checkbutton(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc", variable=self.crawl_all).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=8
        )
        self._path_entry(10, "File lưu kết quả", self.output_path, self._browse_output)
        self._path_entry(11, "File DB lịch sử", self.history_db_path, self._browse_history_db)
        ttk.Checkbutton(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=12, column=0, columnspan=2, sticky="w", pady=8)

        actions = ttk.Frame(self)
        actions.grid(row=13, column=0, columnspan=2, sticky="ew", pady=(16, 8))
        actions.columnconfigure(2, weight=1)
        self.run_button = ttk.Button(actions, text="Bắt đầu crawl", command=self._start_crawl)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ttk.Button(actions, text="Dừng và xuất file", command=self._stop_crawl, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(actions, text="Clear bộ lọc", command=self._clear_filters).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ttk.Label(actions, textvariable=self.status).grid(row=0, column=3, sticky="e")

        ttk.Label(self, text="Log").grid(row=14, column=0, sticky="nw", pady=(8, 4))
        self.log = tk.Text(self, height=14, wrap="word", state="disabled")
        self.log.grid(row=15, column=0, columnspan=2, sticky="nsew")
        self.rowconfigure(15, weight=1)

    def _field_frame(self, row: int, column: int, label: str) -> ttk.Frame:
        frame = ttk.Frame(self)
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 14) if column == 0 else (0, 0), pady=5)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text=label).grid(row=0, column=0, sticky="w", pady=(0, 4))
        return frame

    def _entry_field(self, row: int, column: int, label: str, variable: tk.StringVar, placeholder: str) -> ttk.Entry:
        frame = self._field_frame(row, column, label)
        entry = ttk.Entry(frame, textvariable=variable)
        entry.grid(row=1, column=0, sticky="ew")
        return entry

    def _combo_field(
        self,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        values: list[str] | None = None,
    ) -> ttk.Combobox:
        frame = self._field_frame(row, column, label)
        combo = ttk.Combobox(frame, textvariable=variable, state="readonly", values=values or ["Tất cả"])
        combo.grid(row=1, column=0, sticky="ew")
        return combo

    def _date_range_field(
        self,
        row: int,
        column: int,
        label: str,
        start_variable: tk.StringVar,
        end_variable: tk.StringVar,
    ) -> tuple[DateEntry, DateEntry]:
        frame = self._field_frame(row, column, label)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(2, weight=1)
        start = self._inline_date_entry(frame, start_variable)
        start.grid(row=1, column=0, sticky="ew")
        ttk.Label(frame, text="→").grid(row=1, column=1, padx=6)
        end = self._inline_date_entry(frame, end_variable)
        end.grid(row=1, column=2, sticky="ew")
        return start, end

    def _inline_date_entry(self, parent: ttk.Frame, variable: tk.StringVar) -> DateEntry:
        initial_value = variable.get().strip()
        parsed = datetime.strptime(initial_value, "%d/%m/%Y") if initial_value else datetime.now()
        entry = DateEntry(
            parent,
            textvariable=variable,
            date_pattern="dd/MM/yyyy",
            locale="vi_VN",
            width=12,
            year=parsed.year,
            month=parsed.month,
            day=parsed.day,
        )
        if not initial_value:
            entry.delete(0, "end")
            variable.set("")
        return entry

    def _price_range_field(self, row: int, column: int) -> None:
        frame = self._field_frame(row, column, "Giá khởi điểm")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(2, weight=1)
        ttk.Entry(frame, textvariable=self.auction_from_price).grid(row=1, column=0, sticky="ew")
        ttk.Label(frame, text="→").grid(row=1, column=1, padx=6)
        ttk.Entry(frame, textvariable=self.auction_to_price).grid(row=1, column=2, sticky="ew")

    def _load_initial_auction_options(self) -> None:
        def worker() -> None:
            try:
                client = DGTSCrawlerClient()
                self._auction_option_events.put(
                    (
                        "initial",
                        {
                            "provinces": client.list_provinces(),
                            "property_types": client.list_property_types(),
                            "orgs": client.list_auction_orgs(),
                        },
                    )
                )
            except Exception as exc:
                self._auction_option_events.put(("error", f"Không tải được bộ lọc Tab 1: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_auction_province_selected(self) -> None:
        province_id = self._auction_province_options.get(self.auction_province.get(), "")
        self.auction_district.set("Tất cả")
        self.auction_selected_org.set("Tất cả")
        self._auction_district_options = {"Tất cả": ""}
        self._set_combo_values(self.auction_district_combo, self._auction_district_options)
        if self.auction_district_combo:
            self.auction_district_combo.configure(state="disabled" if not province_id else "readonly")
        if not province_id:
            self._load_initial_auction_options()
            return

        def worker() -> None:
            try:
                client = DGTSCrawlerClient()
                self._auction_option_events.put(
                    (
                        "province",
                        {
                            "districts": client.list_districts(province_id),
                            "orgs": client.list_orgs_by_city(province_id),
                        },
                    )
                )
            except Exception as exc:
                self._auction_option_events.put(("error", f"Không tải được quận/huyện hoặc tổ chức theo tỉnh: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _load_initial_select_org_options(self) -> None:
        def worker() -> None:
            try:
                client = DGTSCrawlerClient()
                self._select_org_option_events.put(
                    (
                        "initial",
                        {
                            "provinces": client.list_provinces(),
                            "property_types": client.list_property_types(),
                        },
                    )
                )
            except Exception as exc:
                self._select_org_option_events.put(("error", f"Không tải được bộ lọc Tab 2: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_select_org_province_selected(self) -> None:
        province_id = self._select_org_province_options.get(self.select_org_province.get(), "")
        self.select_org_district.set("Tất cả")
        self._select_org_district_options = {"Tất cả": ""}
        self._set_combo_values(self.select_org_district_combo, self._select_org_district_options)
        if self.select_org_district_combo:
            self.select_org_district_combo.configure(state="disabled" if not province_id else "readonly")
        if not province_id:
            return

        def worker() -> None:
            try:
                client = DGTSCrawlerClient()
                self._select_org_option_events.put(
                    ("province", {"districts": client.list_districts(province_id)})
                )
            except Exception as exc:
                self._select_org_option_events.put(("error", f"Không tải được quận/huyện Tab 2: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_select_org_option_events(self) -> None:
        while True:
            try:
                kind, payload = self._select_org_option_events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self._append_log(str(payload))
            elif kind == "initial" and isinstance(payload, dict):
                self._select_org_province_options = _option_map(payload.get("provinces") or [], "name")
                self._select_org_property_type_options = _option_map(payload.get("property_types") or [], "name")
                self._set_combo_values(self.select_org_province_combo, self._select_org_province_options)
                self._set_combo_values(self.select_org_property_type_combo, self._select_org_property_type_options)
            elif kind == "province" and isinstance(payload, dict):
                self._select_org_district_options = _option_map(payload.get("districts") or [], "name")
                self._set_combo_values(self.select_org_district_combo, self._select_org_district_options)

    def _drain_auction_option_events(self) -> None:
        while True:
            try:
                kind, payload = self._auction_option_events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self._append_log(str(payload))
            elif kind == "initial" and isinstance(payload, dict):
                self._auction_province_options = _option_map(payload.get("provinces") or [], "name")
                self._auction_property_type_options = _option_map(payload.get("property_types") or [], "name")
                self._auction_org_options = _option_map(payload.get("orgs") or [], "fullname")
                self._set_combo_values(self.auction_province_combo, self._auction_province_options)
                self._set_combo_values(self.auction_property_type_combo, self._auction_property_type_options)
                self._set_combo_values(self.auction_org_combo, self._auction_org_options)
            elif kind == "province" and isinstance(payload, dict):
                self._auction_district_options = _option_map(payload.get("districts") or [], "name")
                self._auction_org_options = _option_map(payload.get("orgs") or [], "fullname")
                self._set_combo_values(self.auction_district_combo, self._auction_district_options)
                self._set_combo_values(self.auction_org_combo, self._auction_org_options)

    def _load_initial_result_options(self) -> None:
        def worker() -> None:
            try:
                client = DGTSCrawlerClient()
                self._result_option_events.put(
                    (
                        "initial",
                        {
                            "provinces": client.list_provinces(),
                            "property_types": client.list_property_types(),
                            "orgs": client.list_auction_orgs(),
                        },
                    )
                )
            except Exception as exc:
                self._result_option_events.put(("error", f"Không tải được bộ lọc Tab 3: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_result_province_selected(self) -> None:
        province_id = self._result_province_options.get(self.result_province.get(), "")
        self.result_district.set("Tất cả")
        self._result_district_options = {"Tất cả": ""}
        self._set_combo_values(self.result_district_combo, self._result_district_options)
        if self.result_district_combo:
            self.result_district_combo.configure(state="disabled" if not province_id else "readonly")
        if not province_id:
            return

        def worker() -> None:
            try:
                client = DGTSCrawlerClient()
                self._result_option_events.put(
                    ("province", {"districts": client.list_districts(province_id)})
                )
            except Exception as exc:
                self._result_option_events.put(("error", f"Không tải được quận/huyện Tab 3: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_result_option_events(self) -> None:
        while True:
            try:
                kind, payload = self._result_option_events.get_nowait()
            except queue.Empty:
                break
            if kind == "error":
                self._append_log(str(payload))
            elif kind == "initial" and isinstance(payload, dict):
                self._result_province_options = _option_map(payload.get("provinces") or [], "name")
                self._result_property_type_options = _option_map(payload.get("property_types") or [], "name")
                self._result_org_options = _option_map(payload.get("orgs") or [], "fullname")
                self._set_combo_values(self.result_province_combo, self._result_province_options)
                self._set_combo_values(self.result_property_type_combo, self._result_property_type_options)
                self._set_combo_values(self.result_org_combo, self._result_org_options)
            elif kind == "province" and isinstance(payload, dict):
                self._result_district_options = _option_map(payload.get("districts") or [], "name")
                self._set_combo_values(self.result_district_combo, self._result_district_options)

    def _set_combo_values(self, combo: ttk.Combobox | None, options: dict[str, str]) -> None:
        if combo is None:
            return
        values = list(options)
        combo.configure(values=values)
        if combo.get() not in values:
            combo.set("Tất cả")

    def _entry(self, row: int, label: str, variable: tk.StringVar, hint: str) -> None:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Label(self, text=hint, foreground="#666666").grid(row=row, column=2, sticky="w", padx=(10, 0), pady=5)

    def _date_entry(self, row: int, label: str, variable: tk.StringVar) -> DateEntry:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
        parsed = datetime.strptime(variable.get(), "%d/%m/%Y")
        entry = DateEntry(
            self,
            textvariable=variable,
            date_pattern="dd/MM/yyyy",
            locale="vi_VN",
            width=18,
            year=parsed.year,
            month=parsed.month,
            day=parsed.day,
        )
        entry.grid(row=row, column=1, sticky="w", pady=5)
        ttk.Label(self, text="Chọn từ lịch", foreground="#666666").grid(
            row=row, column=2, sticky="w", padx=(10, 0), pady=5
        )
        return entry

    def _preset_buttons(self, row: int) -> None:
        ttk.Label(self, text="Chọn nhanh").grid(row=row, column=0, sticky="w", pady=5)
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=1, sticky="w", pady=5)
        ttk.Button(buttons, text="7 ngày gần nhất", command=self._set_last_7_days).pack(side="left")
        ttk.Button(buttons, text="Hôm qua", command=self._set_yesterday).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Hôm nay", command=self._set_today).pack(side="left", padx=(8, 0))
        ttk.Label(self, text="Mặc định là 7 ngày gần nhất", foreground="#666666").grid(
            row=row, column=2, sticky="w", padx=(10, 0), pady=5
        )

    def _set_date_range(self, start: datetime, end: datetime) -> None:
        self.from_date.set(start.strftime("%d/%m/%Y"))
        self.to_date.set(end.strftime("%d/%m/%Y"))
        if self.from_date_picker:
            self.from_date_picker.set_date(start)
        if self.to_date_picker:
            self.to_date_picker.set_date(end)

    def _set_last_7_days(self) -> None:
        today = datetime.now()
        self._set_date_range(today - timedelta(days=7), today)

    def _set_yesterday(self) -> None:
        yesterday = datetime.now() - timedelta(days=1)
        self._set_date_range(yesterday, yesterday)

    def _set_today(self) -> None:
        today = datetime.now()
        self._set_date_range(today, today)

    def _path_entry(self, row: int, label: str, variable: tk.StringVar, command: object) -> None:
        ttk.Label(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
        ttk.Entry(self, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(self, text="Chọn...", command=command).grid(row=row, column=2, sticky="ew", padx=(10, 0), pady=5)

    def _browse_output(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
        if path:
            self.output_path.set(path)

    def _browse_history_db(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".sqlite",
            filetypes=[("SQLite database", "*.sqlite"), ("Database files", "*.db"), ("All files", "*.*")],
        )
        if path:
            self.history_db_path.set(path)

    def _start_crawl(self) -> None:
        try:
            config = self._read_config()
        except ValueError as exc:
            messagebox.showerror("Cấu hình chưa hợp lệ", str(exc))
            return

        errors = validate_config(config)
        if errors:
            messagebox.showerror("Cấu hình chưa hợp lệ", "\n".join(errors))
            return
        if not self.coordinator.try_start(self.run_label):
            message = f"Đang có {self.coordinator.active_label} crawl. Vui lòng chờ tab đó hoàn tất hoặc dừng trước."
            self._append_log(message)
            messagebox.showwarning("Đang crawl", message)
            return

        self.run_id += 1
        self._clear_events()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.stop_event.clear()
        self.status.set("Đang crawl...")
        self._append_log("Bắt đầu crawl")
        worker = threading.Thread(target=self._run_worker, args=(config, self.run_id), daemon=True)
        worker.start()

    def _stop_crawl(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Đang dừng...")
        self._append_log("Đã yêu cầu dừng. Tool sẽ xuất file với dữ liệu đã crawl được.")

    def _read_config(self) -> CrawlerConfig:
        try:
            max_pages = int(self.max_pages.get())
            page_size = int(self.page_size.get())
            detail_workers = int(self.detail_workers.get())
        except ValueError as exc:
            raise ValueError("Số trang tối đa, số bản ghi mỗi trang và số luồng tải chi tiết phải là số nguyên.") from exc
        from_date = self.from_date.get().strip()
        to_date = self.to_date.get().strip()
        if self.notice_kind == "auction":
            from_date = self.auction_start_publish_date.get().strip()
            to_date = self.auction_end_publish_date.get().strip()
        elif self.notice_kind == "select-org":
            from_date = self.select_org_start_date.get().strip()
            to_date = self.select_org_end_date.get().strip()
        return CrawlerConfig(
            from_date=from_date,
            to_date=to_date,
            max_pages=max_pages,
            page_size=page_size,
            detail_workers=detail_workers,
            crawl_all=self.crawl_all.get(),
            notice_kind=self.notice_kind,
            output_path=Path(self.output_path.get().strip()),
            history_db_path=Path(self.history_db_path.get().strip()),
            enable_history=self.enable_history.get(),
            auction_filters=self._read_auction_filters(),
            select_org_filters=self._read_select_org_filters(),
            select_org_result_filters=self._read_select_org_result_filters(),
        )

    def _read_auction_filters(self) -> AuctionFilters:
        if self.notice_kind != "auction":
            return AuctionFilters()
        return AuctionFilters(
            selected_organization_id=self._auction_org_options.get(self.auction_selected_org.get(), ""),
            full_name=self.auction_full_name.get().strip(),
            start_date=self.auction_start_date.get().strip(),
            end_date=self.auction_end_date.get().strip(),
            start_publish_date=self.auction_start_publish_date.get().strip(),
            end_publish_date=self.auction_end_publish_date.get().strip(),
            province_id=self._auction_province_options.get(self.auction_province.get(), ""),
            district_id=self._auction_district_options.get(self.auction_district.get(), ""),
            from_first_price=self.auction_from_price.get().strip(),
            to_first_price=self.auction_to_price.get().strip(),
            property_type_id=self._auction_property_type_options.get(self.auction_property_type.get(), ""),
            type_order="1" if self.auction_type_order.get() == "Ngày tổ chức đấu giá" else "2",
        )

    def _read_select_org_filters(self) -> SelectOrgFilters:
        if self.notice_kind != "select-org":
            return SelectOrgFilters()
        return SelectOrgFilters(
            owner_fullname=self.select_org_owner_fullname.get().strip(),
            start_date=self.select_org_start_date.get().strip(),
            end_date=self.select_org_end_date.get().strip(),
            province_id=self._select_org_province_options.get(self.select_org_province.get(), ""),
            district_id=self._select_org_district_options.get(self.select_org_district.get(), ""),
            property_type_id=self._select_org_property_type_options.get(self.select_org_property_type.get(), ""),
        )

    def _read_select_org_result_filters(self) -> SelectOrgResultFilters:
        if self.notice_kind != "select-org-result":
            return SelectOrgResultFilters()
        return SelectOrgResultFilters(
            owner_fullname=self.result_owner_fullname.get().strip(),
            org_id=self._result_org_options.get(self.result_org.get(), ""),
            publish_start_date=self.result_publish_start_date.get().strip(),
            publish_end_date=self.result_publish_end_date.get().strip(),
            province_id=self._result_province_options.get(self.result_province.get(), ""),
            district_id=self._result_district_options.get(self.result_district.get(), ""),
            property_type_id=self._result_property_type_options.get(self.result_property_type.get(), ""),
        )

    def _run_worker(self, config: CrawlerConfig, run_id: int) -> None:
        try:
            output, _count = run_crawl(
                config,
                progress=lambda msg: self.events.put((run_id, "log", msg)),
                should_stop=self.stop_event.is_set,
            )
            self.events.put((run_id, "done", format_done_message(output)))
        except Exception as exc:
            self.events.put((run_id, "error", str(exc)))

    def _drain_events(self) -> None:
        if self.notice_kind == "auction":
            self._drain_auction_option_events()
        elif self.notice_kind == "select-org":
            self._drain_select_org_option_events()
        elif self.notice_kind == "select-org-result":
            self._drain_result_option_events()
        batch_count = 0
        while batch_count < 50:
            try:
                run_id, kind, message = self.events.get_nowait()
            except queue.Empty:
                break
            if run_id != self.run_id:
                continue
            batch_count += 1
            if kind == "log":
                self._log_batch.append(message)
            elif kind == "done":
                self._log_batch.append(message)
                self._flush_log_batch()
                self.status.set("Hoàn tất")
                self.run_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.coordinator.finish(self.run_label)
                messagebox.showinfo("Hoàn tất", message)
            elif kind == "error":
                self._log_batch.append(f"Lỗi: {message}")
                self._flush_log_batch()
                self.status.set("Lỗi")
                self.run_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.coordinator.finish(self.run_label)
                messagebox.showerror("Lỗi", message)
        self._flush_log_batch()
        self.after(250, self._drain_events)

    def _clear_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                break

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _flush_log_batch(self) -> None:
        if not self._log_batch:
            return
        self.log.configure(state="normal")
        self.log.insert("end", "\n".join(self._log_batch) + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")
        self._log_batch.clear()

    def _clear_filters(self) -> None:
        """Reset all filter fields to their default values."""
        today = datetime.now()
        if self.notice_kind == "auction":
            self.auction_selected_org.set("Tất cả")
            self.auction_full_name.set("")
            self.auction_start_date.set("")
            self.auction_end_date.set("")
            self.auction_start_publish_date.set((today - timedelta(days=7)).strftime("%d/%m/%Y"))
            self.auction_end_publish_date.set(today.strftime("%d/%m/%Y"))
            if self.auction_start_date_picker:
                self.auction_start_date_picker.delete(0, "end")
            if self.auction_end_date_picker:
                self.auction_end_date_picker.delete(0, "end")
            if self.auction_start_publish_date_picker:
                self.auction_start_publish_date_picker.set_date(today - timedelta(days=7))
            if self.auction_end_publish_date_picker:
                self.auction_end_publish_date_picker.set_date(today)
            self.auction_province.set("Tất cả")
            self.auction_district.set("Tất cả")
            self.auction_from_price.set("")
            self.auction_to_price.set("")
            self.auction_type_order.set("Ngày công khai việc đấu giá")
            self.auction_property_type.set("Tất cả")
            self._auction_district_options = {"Tất cả": ""}
            self._set_combo_values(self.auction_district_combo, self._auction_district_options)
            if self.auction_district_combo:
                self.auction_district_combo.configure(state="disabled")
            self._load_initial_auction_options()
        elif self.notice_kind == "select-org":
            self.select_org_owner_fullname.set("")
            self.select_org_start_date.set("")
            self.select_org_end_date.set("")
            if self.select_org_start_date_picker:
                self.select_org_start_date_picker.delete(0, "end")
            if self.select_org_end_date_picker:
                self.select_org_end_date_picker.delete(0, "end")
            self.select_org_province.set("Tất cả")
            self.select_org_district.set("Tất cả")
            self.select_org_property_type.set("Tất cả")
            self._select_org_district_options = {"Tất cả": ""}
            self._set_combo_values(self.select_org_district_combo, self._select_org_district_options)
            if self.select_org_district_combo:
                self.select_org_district_combo.configure(state="disabled")
        elif self.notice_kind == "select-org-result":
            self.result_owner_fullname.set("")
            self.result_org.set("Tất cả")
            self.result_publish_start_date.set("")
            self.result_publish_end_date.set("")
            if self.result_publish_start_picker:
                self.result_publish_start_picker.delete(0, "end")
            if self.result_publish_end_picker:
                self.result_publish_end_picker.delete(0, "end")
            self.result_province.set("Tất cả")
            self.result_district.set("Tất cả")
            self.result_property_type.set("Tất cả")
            self._result_district_options = {"Tất cả": ""}
            self._set_combo_values(self.result_district_combo, self._result_district_options)
            if self.result_district_combo:
                self.result_district_combo.configure(state="disabled")
        else:
            self.from_date.set((today - timedelta(days=7)).strftime("%d/%m/%Y"))
            self.to_date.set(today.strftime("%d/%m/%Y"))
            if self.from_date_picker:
                self.from_date_picker.set_date(today - timedelta(days=7))
            if self.to_date_picker:
                self.to_date_picker.set_date(today)
        self.max_pages.set("10")
        self.page_size.set(DEFAULT_UI_PAGE_SIZE)
        self.detail_workers.set(DEFAULT_DETAIL_WORKERS)
        self.crawl_all.set(False)


class HistoryTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook) -> None:
        super().__init__(parent, padding=16)
        self.history_db_path = tk.StringVar(value=str(Path("outputs") / "dgts_history.sqlite"))
        self.notice_kind_label = tk.StringVar(value="Tất cả")
        self.event_label = tk.StringVar(value="Tất cả")
        self.status = tk.StringVar(value="Sẵn sàng")
        self.rows: list[HistoryEventRow] = []
        self.page_index = 0

        self._build_layout()

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(5, weight=1)

        ttk.Label(self, text="Lịch sử thay đổi", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 14)
        )
        ttk.Label(self, text="File DB lịch sử").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(self, textvariable=self.history_db_path).grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Button(self, text="Chọn...", command=self._browse_history_db).grid(
            row=1, column=2, sticky="ew", padx=(10, 0), pady=5
        )

        ttk.Label(self, text="Loại tin").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Combobox(
            self,
            textvariable=self.notice_kind_label,
            values=list(NOTICE_KIND_OPTIONS),
            state="readonly",
            width=28,
        ).grid(row=2, column=1, sticky="w", pady=5)
        ttk.Label(self, text="Sự kiện").grid(row=2, column=2, sticky="e", padx=(10, 0), pady=5)
        ttk.Combobox(
            self,
            textvariable=self.event_label,
            values=list(EVENT_OPTIONS),
            state="readonly",
            width=18,
        ).grid(row=2, column=3, sticky="w", padx=(10, 0), pady=5)

        actions = ttk.Frame(self)
        actions.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(12, 8))
        ttk.Button(actions, text="Tải lịch sử", command=self._reset_and_load_history).pack(side="left")
        ttk.Button(actions, text="Trang trước", command=self._previous_page).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Trang sau", command=self._next_page).pack(side="left", padx=(10, 0))
        ttk.Button(actions, text="Xuất Excel", command=self._export_excel).pack(side="left", padx=(10, 0))
        ttk.Label(actions, textvariable=self.status).pack(side="right")

        columns = (
            "created_at",
            "notice_kind",
            "event_type",
            "notice_id",
            "publish_date",
            "changed_fields",
            "changed_details",
            "old_values",
            "new_values",
            "matched_notice_id",
            "url",
        )
        self.table = ttk.Treeview(self, columns=columns, show="headings", height=18)
        headings = {
            "created_at": "Thời điểm",
            "notice_kind": "Loại tin",
            "event_type": "Sự kiện",
            "notice_id": "ID",
            "publish_date": "Ngày đăng",
            "changed_fields": "Trường đổi",
            "changed_details": "Chi tiết đổi",
            "old_values": "Cũ",
            "new_values": "Mới",
            "matched_notice_id": "Nghi trùng ID",
            "url": "URL",
        }
        widths = {
            "created_at": 150,
            "notice_kind": 130,
            "event_type": 100,
            "notice_id": 90,
            "publish_date": 90,
            "changed_fields": 180,
            "changed_details": 420,
            "old_values": 320,
            "new_values": 320,
            "matched_notice_id": 110,
            "url": 360,
        }
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(
                column,
                width=widths[column],
                anchor=(
                    "center"
                    if column not in {"url", "changed_details", "old_values", "new_values"}
                    else "w"
                ),
            )
        self.table.grid(row=5, column=0, columnspan=4, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=5, column=4, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)

    def _browse_history_db(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=[("SQLite database", "*.sqlite"), ("Database files", "*.db"), ("All files", "*.*")]
        )
        if path:
            self.history_db_path.set(path)

    def _load_history(self) -> None:
        try:
            store = HistoryStore(Path(self.history_db_path.get().strip()))
            self.rows = store.list_history_rows(
                notice_kind=NOTICE_KIND_OPTIONS[self.notice_kind_label.get()],
                event_type=EVENT_OPTIONS[self.event_label.get()],
                limit=HISTORY_PAGE_SIZE,
                offset=self.page_index * HISTORY_PAGE_SIZE,
            )
        except Exception as exc:
            messagebox.showerror("Không tải được lịch sử", str(exc))
            return

        # Batch update: detach all items then rebuild to reduce flicker
        existing = self.table.get_children()
        if existing:
            self.table.delete(*existing)
        for row in self.rows:
            self.table.insert(
                "",
                "end",
                values=(
                    format_history_table_value(row, "created_at"),
                    format_history_table_value(row, "notice_kind"),
                    format_history_table_value(row, "event_type"),
                    format_history_table_value(row, "notice_id"),
                    format_history_table_value(row, "publish_date"),
                    format_history_table_value(row, "changed_fields"),
                    format_history_table_value(row, "changed_details"),
                    format_history_table_value(row, "old_values"),
                    format_history_table_value(row, "new_values"),
                    format_history_table_value(row, "matched_notice_id"),
                    format_history_table_value(row, "detail_url"),
                ),
            )
        self.status.set(format_history_page_status(self.page_index, len(self.rows)))

    def _reset_and_load_history(self) -> None:
        self.page_index = 0
        self._load_history()

    def _previous_page(self) -> None:
        if self.page_index == 0:
            return
        self.page_index -= 1
        self._load_history()

    def _next_page(self) -> None:
        self.page_index += 1
        self._load_history()
        if not self.rows and self.page_index > 0:
            self.page_index -= 1
            self._load_history()

    def _export_excel(self) -> None:
        if not self.rows:
            self._load_history()
        if not self.rows:
            messagebox.showinfo("Không có dữ liệu", "Không có dòng lịch sử để xuất.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            filetypes=[("Excel files", "*.xlsx")],
            initialfile="lich_su_thay_doi.xlsx",
        )
        if not path:
            return
        try:
            output = write_history_report(path, self.rows)
        except Exception as exc:
            messagebox.showerror("Không xuất được Excel", str(exc))
            return
        messagebox.showinfo("Hoàn tất", f"Đã xuất: {output}")


class CrawlerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DGTS Crawler")
        self.geometry("980x820")
        self.minsize(900, 720)
        self.run_coordinator = RunCoordinator()
        self._build_layout()

    def _build_layout(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True)

        for config in TAB_CONFIGS:
            tab = CrawlerTab(
                notebook,
                config.notice_kind,
                config.output_default,
                config.header,
                config.tab_label,
                self.run_coordinator,
            )
            notebook.add(tab, text=config.tab_label)
        notebook.add(HistoryTab(notebook), text=HISTORY_TAB_LABEL)


def main() -> None:
    app = CrawlerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
