from __future__ import annotations

import queue
import threading
import tkinter as tk
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from tkcalendar import DateEntry

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")

from .client import DGTSCrawlerClient
from .history_report import write_history_report
from .history_store import HistoryEventRow, HistoryStore
from .runner import AuctionFilters, CrawlerConfig, SelectOrgFilters, SelectOrgResultFilters, run_crawl, validate_config


FONT_FAMILY = "Segoe UI Variable"
ACCENT = ("#247A68", "#3A9A83")
ACCENT_HOVER = ("#1D6657", "#31836F")
TEXT = ("#17211F", "#F2F6F4")
MUTED = ("#65716D", "#A7B1AD")
SURFACE = ("#EEF2F0", "#20272A")
PANEL = ("#F7F9F8", "#2A3235")
INPUT = ("#FFFFFF", "#30393D")
BORDER = ("#CAD4D0", "#4B575B")
ERROR = ("#A54848", "#D97878")


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
        tab_label="Đấu giá",
        header="Thông báo công khai việc đấu giá",
    ),
    TabConfig(
        notice_kind="select-org",
        output_default=Path("outputs") / "dgts_select_org_notices.xlsx",
        tab_label="Chọn tổ chức",
        header="Danh sách thông báo lựa chọn tổ chức hành nghề đấu giá",
    ),
    TabConfig(
        notice_kind="select-org-result",
        output_default=Path("outputs") / "dgts_select_org_result_notices.xlsx",
        tab_label="Kết quả lựa chọn",
        header="Danh sách thông báo kết quả lựa chọn tổ chức hành nghề đấu giá",
    ),
]
HISTORY_TAB_LABEL = "Lịch sử thay đổi"
NOTICE_KIND_OPTIONS = {
    "Tất cả": "",
    "Đấu giá": "auction",
    "Lựa chọn tổ chức": "select-org",
    "Kết quả lựa chọn": "select-org-result",
}
EVENT_OPTIONS = {
    "Tất cả": "",
    "NEW": "NEW",
    "CHANGED": "CHANGED",
    "MISSING": "MISSING",
    "REAPPEARED": "REAPPEARED",
    "SUSPECT_REPOST": "SUSPECT_REPOST",
    "SAME_ASSET_NAME": "SAME_ASSET_NAME",
    "SECOND_PUBLICATION": "SECOND_PUBLICATION",
    "REPUBLISHED_EXPECTED": "REPUBLISHED_EXPECTED",
    "REPUBLISHED_CHANGED": "REPUBLISHED_CHANGED",
    "DELISTED": "DELISTED",
    "REMOVAL_PENDING": "REMOVAL_PENDING",
    "REMOVED": "REMOVED",
    "CHECK_FAILED": "CHECK_FAILED",
}
DEFAULT_UI_PAGE_SIZE = "10"
DEFAULT_DETAIL_WORKERS = "5"
HISTORY_PAGE_SIZE = 500
MAX_SUGGESTION_ROWS = 10


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


def _search_key(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value.strip().casefold()).replace("đ", "d")
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _filter_option_labels(labels: list[str], typed_text: str) -> list[str]:
    query = _search_key(typed_text)
    if not query:
        return labels
    return [label for label in labels if label == "Tất cả" or query in _search_key(label)]


def _suggestion_popup_height(item_count: int) -> int:
    return max(1, min(MAX_SUGGESTION_ROWS, item_count))


def _focus_is_within_combo_suggestion(focus: object | None, combo: object, popup: object) -> bool:
    if focus is None:
        return False
    if focus is getattr(combo, "_entry", None):
        return True
    try:
        return focus.winfo_toplevel() is popup
    except Exception:
        return False


def _raise_suggestion_popup(popup: object) -> None:
    popup.lift()
    popup.attributes("-topmost", True)
    popup.after(50, lambda: popup.attributes("-topmost", False))


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


class CrawlerTab(ctk.CTkFrame):
    def __init__(
        self,
        parent: ctk.CTkFrame,
        notice_kind: str,
        output_default: Path,
        header: str,
        run_label: str,
        coordinator: RunCoordinator,
    ) -> None:
        super().__init__(parent, fg_color="transparent")
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
        self.select_org_province_combo: ctk.CTkComboBox | None = None
        self.select_org_district_combo: ctk.CTkComboBox | None = None
        self.select_org_property_type_combo: ctk.CTkComboBox | None = None
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
        self.result_org_combo: ctk.CTkComboBox | None = None
        self.result_province_combo: ctk.CTkComboBox | None = None
        self.result_district_combo: ctk.CTkComboBox | None = None
        self.result_property_type_combo: ctk.CTkComboBox | None = None
        # --- Shared ---
        self.max_pages = tk.StringVar(value="10")
        self.page_size = tk.StringVar(value=DEFAULT_UI_PAGE_SIZE)
        self.detail_workers = tk.StringVar(value=DEFAULT_DETAIL_WORKERS)
        self.crawl_all = tk.BooleanVar(value=False)
        self.output_path = tk.StringVar(value=str(output_default))
        self.history_db_path = tk.StringVar(value=str(Path("outputs") / "dgts_history.sqlite"))
        self.enable_history = tk.BooleanVar(value=True)
        self.enable_screenshots = tk.BooleanVar(value=False)
        self.screenshot_dir = tk.StringVar(value=str(Path("outputs") / "screenshots"))
        self.status = tk.StringVar(value="Sẵn sàng")
        self.progress_bar: ctk.CTkProgressBar | None = None
        self.status_label: ctk.CTkLabel | None = None
        self.events: queue.Queue[tuple[int, str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self.run_id = 0
        self.from_date_picker: DateEntry | None = None
        self.to_date_picker: DateEntry | None = None
        self.auction_start_date_picker: DateEntry | None = None
        self.auction_end_date_picker: DateEntry | None = None
        self.auction_start_publish_date_picker: DateEntry | None = None
        self.auction_end_publish_date_picker: DateEntry | None = None
        self.auction_org_combo: ctk.CTkComboBox | None = None
        self.auction_province_combo: ctk.CTkComboBox | None = None
        self.auction_district_combo: ctk.CTkComboBox | None = None
        self.auction_property_type_combo: ctk.CTkComboBox | None = None

        self._log_batch: list[str] = []
        self._active_suggestion_popup: tk.Toplevel | None = None
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

    def _section_header(self, row: int, title: str, description: str) -> None:
        section = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=8, height=42)
        section.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(18, 8))
        section.grid_propagate(False)
        section.columnconfigure(1, weight=1)
        ctk.CTkLabel(
            section,
            text=title,
            font=(FONT_FAMILY, 13, "bold"),
            text_color=TEXT,
        ).grid(row=0, column=0, sticky="w", padx=(14, 10), pady=10)
        ctk.CTkLabel(
            section,
            text=description,
            font=(FONT_FAMILY, 11),
            text_color=MUTED,
        ).grid(row=0, column=1, sticky="w", pady=10)

    def _build_action_area(self, row: int) -> None:
        actions = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10, border_width=1, border_color=BORDER)
        actions.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(20, 10))
        actions.columnconfigure(3, weight=1)
        self.run_button = ctk.CTkButton(
            actions,
            text="▶  Bắt đầu crawl",
            command=self._start_crawl,
            width=154,
            height=38,
            font=(FONT_FAMILY, 13, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        )
        self.run_button.grid(row=0, column=0, sticky="w", padx=(12, 0), pady=(12, 8))
        self.stop_button = ctk.CTkButton(
            actions,
            text="■  Dừng và xuất file",
            command=self._stop_crawl,
            state="disabled",
            width=156,
            height=38,
            fg_color="transparent",
            hover_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
        )
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(10, 0), pady=(12, 8))
        ctk.CTkButton(
            actions,
            text="Đặt lại bộ lọc",
            command=self._clear_filters,
            width=124,
            height=38,
            fg_color="transparent",
            hover_color=SURFACE,
            text_color=MUTED,
        ).grid(row=0, column=2, sticky="w", padx=(4, 0), pady=(12, 8))

        status_box = ctk.CTkFrame(actions, fg_color="transparent")
        status_box.grid(row=0, column=3, sticky="e", padx=14, pady=(12, 8))
        ctk.CTkLabel(status_box, text="TRẠNG THÁI", font=(FONT_FAMILY, 9, "bold"), text_color=MUTED).pack(
            anchor="e"
        )
        self.status_label = ctk.CTkLabel(
            status_box,
            textvariable=self.status,
            font=(FONT_FAMILY, 12, "bold"),
            text_color=TEXT,
        )
        self.status_label.pack(anchor="e")
        self.progress_bar = ctk.CTkProgressBar(
            actions,
            mode="indeterminate",
            height=4,
            corner_radius=2,
            progress_color=ACCENT,
            fg_color=SURFACE,
        )
        self.progress_bar.grid(row=1, column=0, columnspan=4, sticky="ew", padx=12, pady=(0, 11))
        self.progress_bar.set(0)

    def _set_status_visual(self, state: str) -> None:
        if self.status_label is None or self.progress_bar is None:
            return
        if state == "running":
            self.status_label.configure(text_color=ACCENT)
            self.progress_bar.start()
        elif state == "error":
            self.status_label.configure(text_color=ERROR)
            self.progress_bar.stop()
            self.progress_bar.set(1)
            self.progress_bar.configure(progress_color=ERROR)
        else:
            self.status_label.configure(text_color=TEXT)
            self.progress_bar.stop()
            self.progress_bar.configure(progress_color=ACCENT)
            self.progress_bar.set(1 if state == "done" else 0)

    def _build_simple_layout(self) -> None:
        """Simple date-range-only layout used by Tab 3 (select-org-result)."""
        self.columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=self.header, font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        self.from_date_picker = self._date_entry(1, "Từ ngày", self.from_date)
        self.to_date_picker = self._date_entry(2, "Đến ngày", self.to_date)
        self._preset_buttons(3)
        self._entry(4, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(5, "Số bản ghi mỗi trang", self.page_size, "Mặc định 10 bản ghi để crawl nhanh hơn")
        self._entry(6, "Số luồng tải chi tiết", self.detail_workers, "Mặc định 5 luồng")
        ctk.CTkCheckBox(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc ngày", variable=self.crawl_all).grid(
            row=7, column=1, sticky="w", pady=8
        )
        self._path_entry(8, "File lưu kết quả", self.output_path, self._browse_output)
        self._path_entry(9, "File DB lịch sử", self.history_db_path, self._browse_history_db)
        ctk.CTkCheckBox(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=10, column=1, sticky="w", pady=8)
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(16, 8))
        actions.columnconfigure(2, weight=1)
        self.run_button = ctk.CTkButton(actions, text="Bắt đầu crawl", command=self._start_crawl, width=120)
        self.run_button.grid(row=0, column=0, sticky="w")
        self.stop_button = ctk.CTkButton(actions, text="Dừng và xuất file", command=self._stop_crawl, state="disabled", width=140)
        self.stop_button.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ctk.CTkButton(actions, text="Clear bộ lọc", command=self._clear_filters, width=110).grid(
            row=0, column=2, sticky="w", padx=(10, 0)
        )
        ctk.CTkLabel(actions, textvariable=self.status).grid(row=0, column=3, sticky="e")
        ctk.CTkLabel(self, text="Log").grid(row=12, column=0, sticky="nw", pady=(8, 4))
        self.log = ctk.CTkTextbox(self, height=220, wrap="word")
        self.log.configure(state="disabled")
        self.log.grid(row=13, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(13, weight=1)

    def _build_select_org_result_layout(self) -> None:
        """Rich filter layout for Tab 3 (select-org-result), matching the DGTS website."""
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=self.header, font=(FONT_FAMILY, 20, "bold"), text_color=TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(4, 8)
        )
        self._section_header(1, "Bộ lọc tìm kiếm", "Thu hẹp kết quả theo chủ tài sản, tổ chức và địa bàn")
        self._entry_field(2, 0, "Người có tài sản", self.result_owner_fullname, "Nhập tên người có tài sản")
        self.result_org_combo = self._combo_field(2, 1, "Tên tổ chức", self.result_org)
        self.result_province_combo = self._combo_field(
            3,
            0,
            "Tỉnh/Thành phố",
            self.result_province,
            command=lambda _val: self._on_result_province_selected(),
        )
        self.result_district_combo = self._combo_field(3, 1, "Quận/Huyện", self.result_district)
        self.result_district_combo.configure(state="disabled")
        self.result_property_type_combo = self._combo_field(4, 0, "Loại tài sản", self.result_property_type)
        self.result_publish_start_picker, self.result_publish_end_picker = self._date_range_field(
            4, 1, "Ngày công khai (từ → đến)", self.result_publish_start_date, self.result_publish_end_date
        )
        self._section_header(5, "Cấu hình crawl", "Điều chỉnh phạm vi và tốc độ thu thập")
        self._entry(6, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(7, "Số bản ghi mỗi trang", self.page_size, "Mặc định: 10 bản ghi")
        self._entry(8, "Số luồng tải chi tiết", self.detail_workers, "Mặc định: 5 luồng")
        ctk.CTkCheckBox(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc", variable=self.crawl_all).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )
        self._section_header(10, "Lưu trữ & bằng chứng", "Chọn nơi lưu snapshot, lịch sử và ảnh chụp")
        self._path_entry(11, "File lưu kết quả", self.output_path, self._browse_output, "📄  Chọn file")
        self._path_entry(12, "File DB lịch sử", self.history_db_path, self._browse_history_db, "▣  Chọn DB")
        ctk.CTkCheckBox(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self._screenshot_options(14)
        self._build_action_area(15)
        ctk.CTkLabel(self, text="Nhật ký hoạt động", font=(FONT_FAMILY, 12, "bold"), text_color=TEXT).grid(
            row=16, column=0, sticky="nw", pady=(8, 6)
        )
        self.log = ctk.CTkTextbox(self, height=220, wrap="word")
        self.log.configure(state="disabled")
        self.log.grid(row=17, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(17, weight=1)

    def _build_select_org_layout(self) -> None:
        """Rich filter layout for Tab 2 (select-org), matching the DGTS website."""
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=self.header, font=(FONT_FAMILY, 20, "bold"), text_color=TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(4, 8)
        )
        self._section_header(1, "Bộ lọc tìm kiếm", "Thu hẹp thông báo theo chủ tài sản, thời gian và địa bàn")
        self._entry_field(2, 0, "Người có tài sản", self.select_org_owner_fullname, "Nhập tên người có tài sản")
        self.select_org_start_date_picker, self.select_org_end_date_picker = self._date_range_field(
            2, 1, "Thời gian nộp hồ sơ", self.select_org_start_date, self.select_org_end_date
        )
        self.select_org_province_combo = self._combo_field(
            3,
            0,
            "Tỉnh/Thành phố",
            self.select_org_province,
            command=lambda _val: self._on_select_org_province_selected(),
        )
        self.select_org_district_combo = self._combo_field(3, 1, "Quận/Huyện", self.select_org_district)
        self.select_org_district_combo.configure(state="disabled")
        self.select_org_property_type_combo = self._combo_field(4, 0, "Loại tài sản", self.select_org_property_type)
        self._section_header(5, "Cấu hình crawl", "Điều chỉnh phạm vi và tốc độ thu thập")
        self._entry(6, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(7, "Số bản ghi mỗi trang", self.page_size, "Mặc định: 10 bản ghi")
        self._entry(8, "Số luồng tải chi tiết", self.detail_workers, "Mặc định: 5 luồng")
        ctk.CTkCheckBox(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc", variable=self.crawl_all).grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )
        self._section_header(10, "Lưu trữ & bằng chứng", "Chọn nơi lưu snapshot, lịch sử và ảnh chụp")
        self._path_entry(11, "File lưu kết quả", self.output_path, self._browse_output, "📄  Chọn file")
        self._path_entry(12, "File DB lịch sử", self.history_db_path, self._browse_history_db, "▣  Chọn DB")
        ctk.CTkCheckBox(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=13, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self._screenshot_options(14)
        self._build_action_area(15)
        ctk.CTkLabel(self, text="Nhật ký hoạt động", font=(FONT_FAMILY, 12, "bold"), text_color=TEXT).grid(
            row=16, column=0, sticky="nw", pady=(8, 6)
        )
        self.log = ctk.CTkTextbox(self, height=220, wrap="word")
        self.log.configure(state="disabled")
        self.log.grid(row=17, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(17, weight=1)

    def _build_auction_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        ctk.CTkLabel(self, text=self.header, font=(FONT_FAMILY, 20, "bold"), text_color=TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(4, 8)
        )
        self._section_header(1, "Bộ lọc tìm kiếm", "Thu hẹp thông báo theo tổ chức, thời gian và tài sản")
        self.auction_org_combo = self._combo_field(2, 0, "Tổ chức hành nghề đấu giá", self.auction_selected_org)
        self._entry_field(2, 1, "Người có tài sản", self.auction_full_name, "Nhập tên người có tài sản")
        self.auction_start_date_picker, self.auction_end_date_picker = self._date_range_field(
            3, 0, "Thời gian tổ chức cuộc đấu giá", self.auction_start_date, self.auction_end_date
        )
        self.auction_start_publish_date_picker, self.auction_end_publish_date_picker = self._date_range_field(
            3, 1, "Thời gian công khai việc đấu giá", self.auction_start_publish_date, self.auction_end_publish_date
        )
        self.auction_province_combo = self._combo_field(
            4,
            0,
            "Tỉnh thành phố",
            self.auction_province,
            command=lambda _val: self._on_auction_province_selected(),
        )
        self.auction_district_combo = self._combo_field(4, 1, "Quận/huyện", self.auction_district)
        self.auction_district_combo.configure(state="disabled")
        self._price_range_field(5, 0)
        self._combo_field(
            5,
            1,
            "Tiêu chí sắp xếp",
            self.auction_type_order,
            values=["Ngày công khai việc đấu giá", "Ngày tổ chức đấu giá"],
            searchable=False,
        )
        self.auction_property_type_combo = self._combo_field(6, 0, "Loại tài sản", self.auction_property_type)

        self._section_header(7, "Cấu hình crawl", "Điều chỉnh phạm vi và tốc độ thu thập")
        self._entry(8, "Số trang tối đa", self.max_pages, "Bỏ qua khi chọn crawl toàn bộ")
        self._entry(9, "Số bản ghi mỗi trang", self.page_size, "Mặc định: 10 bản ghi")
        self._entry(10, "Số luồng tải chi tiết", self.detail_workers, "Mặc định: 5 luồng")
        ctk.CTkCheckBox(self, text="Crawl toàn bộ dữ liệu khớp bộ lọc", variable=self.crawl_all).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(8, 4)
        )

        self._section_header(12, "Lưu trữ & bằng chứng", "Chọn nơi lưu snapshot, lịch sử và ảnh chụp")
        self._path_entry(13, "File lưu kết quả", self.output_path, self._browse_output, "📄  Chọn file")
        self._path_entry(14, "File DB lịch sử", self.history_db_path, self._browse_history_db, "▣  Chọn DB")
        ctk.CTkCheckBox(
            self,
            text="Lưu lịch sử và phát hiện thay đổi",
            variable=self.enable_history,
        ).grid(row=15, column=0, columnspan=3, sticky="w", pady=(8, 4))
        self._screenshot_options(16)

        self._build_action_area(17)

        ctk.CTkLabel(self, text="Nhật ký hoạt động", font=(FONT_FAMILY, 12, "bold"), text_color=TEXT).grid(
            row=18, column=0, sticky="nw", pady=(8, 6)
        )
        self.log = ctk.CTkTextbox(self, height=220, wrap="word")
        self.log.configure(state="disabled")
        self.log.grid(row=19, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(19, weight=1)

    def _field_frame(self, row: int, column: int, label: str) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=column, sticky="ew", padx=(0, 16) if column == 0 else (0, 0), pady=7)
        frame.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            frame, text=label, font=(FONT_FAMILY, 11, "bold"), text_color=TEXT
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        return frame

    def _entry_field(self, row: int, column: int, label: str, variable: tk.StringVar, placeholder: str) -> ctk.CTkEntry:
        frame = self._field_frame(row, column, label)
        entry = ctk.CTkEntry(
            frame,
            textvariable=variable,
            placeholder_text=placeholder,
            height=32,
            fg_color=INPUT,
            border_color=BORDER,
            text_color=TEXT,
            placeholder_text_color=MUTED,
        )
        entry.grid(row=1, column=0, sticky="ew")
        return entry

    def _combo_field(
        self,
        row: int,
        column: int,
        label: str,
        variable: tk.StringVar,
        values: list[str] | None = None,
        command: object | None = None,
        searchable: bool = True,
    ) -> ctk.CTkComboBox:
        frame = self._field_frame(row, column, label)
        combo = ctk.CTkComboBox(
            frame,
            variable=variable,
            state="normal" if searchable else "readonly",
            values=values or ["Tất cả"],
            command=command,
            height=32,
            fg_color=INPUT,
            border_color=BORDER,
            button_color=BORDER,
            button_hover_color=ACCENT,
            text_color=TEXT,
            dropdown_fg_color=PANEL,
            dropdown_text_color=TEXT,
            dropdown_hover_color=SURFACE,
        )
        if searchable:
            self._make_combo_searchable(combo, variable, command)
        combo.grid(row=1, column=0, sticky="ew")
        return combo

    def _make_combo_searchable(
        self,
        combo: ctk.CTkComboBox,
        variable: tk.StringVar,
        command: object | None = None,
    ) -> None:
        combo._searchable_values = list(combo.cget("values"))  # type: ignore[attr-defined]
        combo._searchable_command = command  # type: ignore[attr-defined]
        combo._open_dropdown_menu = lambda: self._show_combo_suggestions(combo, variable, command, show_all=True)  # type: ignore[method-assign]

        def filter_values(_event: tk.Event) -> None:
            key = getattr(_event, "keysym", "")
            if key in {"Up", "Down", "Left", "Right", "Return", "Tab"}:
                return
            if key == "Escape":
                self._hide_combo_suggestions()
                return
            values = getattr(combo, "_searchable_values", list(combo.cget("values")))
            filtered = _filter_option_labels(values, variable.get())
            combo.configure(values=filtered)
            if filtered:
                self._show_combo_suggestions(combo, variable, command)
            else:
                self._hide_combo_suggestions()

        def apply_exact_match(_event: tk.Event) -> None:
            values = getattr(combo, "_searchable_values", list(combo.cget("values")))
            current = variable.get()
            combo.configure(values=_filter_option_labels(values, current))
            callback = getattr(combo, "_searchable_command", None)
            if callback and current in values:
                callback(current)
                self._hide_combo_suggestions()

        def hide_after_focus_leaves(_event: tk.Event) -> None:
            popup = self._active_suggestion_popup

            def maybe_hide() -> None:
                focus = self.focus_get()
                active_popup = self._active_suggestion_popup
                if active_popup is None or active_popup is not popup:
                    return
                if not _focus_is_within_combo_suggestion(focus, combo, active_popup):
                    self._hide_combo_suggestions()

            self.after(150, maybe_hide)

        combo.bind("<KeyRelease>", filter_values, add=True)
        combo.bind("<Return>", apply_exact_match, add=True)
        combo.bind("<FocusOut>", hide_after_focus_leaves, add=True)

    def _show_combo_suggestions(
        self,
        combo: ctk.CTkComboBox,
        variable: tk.StringVar,
        command: object | None = None,
        show_all: bool = False,
    ) -> None:
        values = getattr(combo, "_searchable_values", list(combo.cget("values")))
        suggestions = values if show_all else _filter_option_labels(values, variable.get())
        if not suggestions:
            self._hide_combo_suggestions()
            return

        self._hide_combo_suggestions()
        popup = tk.Toplevel(self)
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        popup.configure(bg="#343638" if ctk.get_appearance_mode() == "Dark" else "#ffffff")
        self._active_suggestion_popup = popup

        row_count = _suggestion_popup_height(len(suggestions))
        listbox = tk.Listbox(
            popup,
            height=row_count,
            activestyle="none",
            exportselection=False,
            bg="#343638" if ctk.get_appearance_mode() == "Dark" else "#ffffff",
            fg="white" if ctk.get_appearance_mode() == "Dark" else "black",
            selectbackground="#3a9a83" if ctk.get_appearance_mode() == "Dark" else "#247a68",
            selectforeground="white",
            relief="solid",
            borderwidth=1,
            font=(FONT_FAMILY, 10),
        )
        scrollbar = ttk.Scrollbar(popup, orient="vertical", command=listbox.yview)
        listbox.configure(yscrollcommand=scrollbar.set)
        for suggestion in suggestions:
            listbox.insert("end", suggestion)
        listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        popup.columnconfigure(0, weight=1)
        popup.rowconfigure(0, weight=1)

        width = max(combo.winfo_width(), 180)
        item_height = 24
        popup.geometry(f"{width}x{row_count * item_height}+{combo.winfo_rootx()}+{combo.winfo_rooty() + combo.winfo_height()}")
        try:
            _raise_suggestion_popup(popup)
        except Exception:
            pass

        def choose_current(_event: tk.Event | None = None) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            value = str(listbox.get(selection[0]))
            variable.set(value)
            combo.configure(values=_filter_option_labels(values, value))
            self._hide_combo_suggestions()
            callback = command or getattr(combo, "_searchable_command", None)
            if callback:
                callback(value)
            combo.focus_force()

        listbox.bind("<ButtonRelease-1>", choose_current)
        listbox.bind("<Return>", choose_current)
        listbox.bind("<Escape>", lambda _event: self._hide_combo_suggestions())
        combo.focus_force()

    def _hide_combo_suggestions(self) -> None:
        if self._active_suggestion_popup is None:
            return
        try:
            self._active_suggestion_popup.destroy()
        finally:
            self._active_suggestion_popup = None

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
        ctk.CTkLabel(frame, text="→").grid(row=1, column=1, padx=6)
        end = self._inline_date_entry(frame, end_variable)
        end.grid(row=1, column=2, sticky="ew")
        return start, end

    def _inline_date_entry(self, parent: ctk.CTkFrame, variable: tk.StringVar) -> DateEntry:
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
        mode = ctk.get_appearance_mode()
        if mode == "Dark":
            entry.configure(
                background="#30393d",
                foreground="white",
                selectbackground="#3a9a83",
                selectforeground="white",
                headersbackground="#20272a",
                headersforeground="white",
                bordercolor="#4b575b"
            )
        else:
            entry.configure(
                background="#ffffff",
                foreground="black",
                selectbackground="#247a68",
                selectforeground="white",
                headersbackground="#eef2f0",
                headersforeground="black",
                bordercolor="#cad4d0"
            )
        if not initial_value:
            entry.delete(0, "end")
            variable.set("")
        return entry

    def _price_range_field(self, row: int, column: int) -> None:
        frame = self._field_frame(row, column, "Giá khởi điểm")
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(2, weight=1)
        ctk.CTkEntry(
            frame, textvariable=self.auction_from_price, height=32, fg_color=INPUT, border_color=BORDER, text_color=TEXT
        ).grid(row=1, column=0, sticky="ew")
        ctk.CTkLabel(frame, text="→").grid(row=1, column=1, padx=6)
        ctk.CTkEntry(
            frame, textvariable=self.auction_to_price, height=32, fg_color=INPUT, border_color=BORDER, text_color=TEXT
        ).grid(row=1, column=2, sticky="ew")

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
            self.auction_district_combo.configure(state="disabled" if not province_id else "normal")
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
            self.select_org_district_combo.configure(state="disabled" if not province_id else "normal")
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
            self.result_district_combo.configure(state="disabled" if not province_id else "normal")
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

    def _set_combo_values(self, combo: ctk.CTkComboBox | None, options: dict[str, str]) -> None:
        if combo is None:
            return
        values = list(options)
        if hasattr(combo, "_searchable_values"):
            combo._searchable_values = values  # type: ignore[attr-defined]
        combo.configure(values=values)
        if combo.get() not in values:
            combo.set("Tất cả")

    def _entry(self, row: int, label: str, variable: tk.StringVar, hint: str) -> None:
        ctk.CTkLabel(self, text=label, font=(FONT_FAMILY, 11, "bold"), text_color=TEXT).grid(
            row=row, column=0, sticky="w", pady=7
        )
        ctk.CTkEntry(
            self, textvariable=variable, height=32, fg_color=INPUT, border_color=BORDER, text_color=TEXT
        ).grid(row=row, column=1, sticky="ew", pady=7)
        ctk.CTkLabel(self, text=hint, font=(FONT_FAMILY, 10), text_color=MUTED).grid(
            row=row, column=2, sticky="w", padx=(12, 0), pady=7
        )

    def _date_entry(self, row: int, label: str, variable: tk.StringVar) -> DateEntry:
        ctk.CTkLabel(self, text=label).grid(row=row, column=0, sticky="w", pady=5)
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
        mode = ctk.get_appearance_mode()
        if mode == "Dark":
            entry.configure(
                background="#30393d",
                foreground="white",
                selectbackground="#3a9a83",
                selectforeground="white",
                headersbackground="#20272a",
                headersforeground="white",
                bordercolor="#4b575b"
            )
        else:
            entry.configure(
                background="#ffffff",
                foreground="black",
                selectbackground="#247a68",
                selectforeground="white",
                headersbackground="#eef2f0",
                headersforeground="black",
                bordercolor="#cad4d0"
            )
        entry.grid(row=row, column=1, sticky="w", pady=5)
        ctk.CTkLabel(self, text="Chọn từ lịch", text_color="#888888").grid(
            row=row, column=2, sticky="w", padx=(10, 0), pady=5
        )
        return entry

    def _preset_buttons(self, row: int) -> None:
        ctk.CTkLabel(self, text="Chọn nhanh").grid(row=row, column=0, sticky="w", pady=5)
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=row, column=1, sticky="w", pady=5)
        ctk.CTkButton(buttons, text="7 ngày gần nhất", command=self._set_last_7_days, height=28, width=110).pack(side="left")
        ctk.CTkButton(buttons, text="Hôm qua", command=self._set_yesterday, height=28, width=80).pack(side="left", padx=(8, 0))
        ctk.CTkButton(buttons, text="Hôm nay", command=self._set_today, height=28, width=80).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(self, text="Mặc định là 7 ngày gần nhất", text_color="#888888").grid(
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

    def _path_entry(
        self, row: int, label: str, variable: tk.StringVar, command: object, button_text: str = "📄  Chọn file"
    ) -> None:
        ctk.CTkLabel(self, text=label, font=(FONT_FAMILY, 11, "bold"), text_color=TEXT).grid(
            row=row, column=0, sticky="w", pady=7
        )
        ctk.CTkEntry(
            self, textvariable=variable, height=32, fg_color=INPUT, border_color=BORDER, text_color=TEXT
        ).grid(row=row, column=1, sticky="ew", pady=7)
        ctk.CTkButton(
            self,
            text=button_text,
            command=command,
            width=126,
            height=32,
            fg_color="transparent",
            hover_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
        ).grid(row=row, column=2, sticky="ew", padx=(12, 0), pady=7)

    def _screenshot_options(self, row: int) -> None:
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, columnspan=3, sticky="ew", pady=6)
        frame.columnconfigure(1, weight=1)
        ctk.CTkCheckBox(
            frame,
            text="Chụp ảnh từng bài",
            variable=self.enable_screenshots,
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ctk.CTkEntry(
            frame, textvariable=self.screenshot_dir, height=32, fg_color=INPUT, border_color=BORDER, text_color=TEXT
        ).grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(
            frame,
            text="📁  Chọn thư mục",
            command=self._browse_screenshot_dir,
            width=140,
            height=32,
            fg_color="transparent",
            hover_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
        ).grid(
            row=0, column=2, sticky="ew", padx=(10, 0)
        )

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

    def _browse_screenshot_dir(self) -> None:
        path = filedialog.askdirectory()
        if path:
            self.screenshot_dir.set(path)

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
        self._set_status_visual("running")
        self._append_log("Bắt đầu crawl")
        worker = threading.Thread(target=self._run_worker, args=(config, self.run_id), daemon=True)
        worker.start()

    def _stop_crawl(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Đang dừng...")
        self._set_status_visual("running")
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
            enable_screenshots=self.enable_screenshots.get(),
            screenshot_dir=Path(self.screenshot_dir.get().strip()),
            timestamp_output=True,
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
                self._set_status_visual("done")
                self.run_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.coordinator.finish(self.run_label)
                messagebox.showinfo("Hoàn tất", message)
            elif kind == "error":
                self._log_batch.append(f"Lỗi: {message}")
                self._flush_log_batch()
                self.status.set("Lỗi")
                self._set_status_visual("error")
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
        self.enable_screenshots.set(False)


class HistoryTab(ctk.CTkFrame):
    def __init__(self, parent: ctk.CTkFrame) -> None:
        super().__init__(parent, fg_color="transparent")
        self.history_db_path = tk.StringVar(value=str(Path("outputs") / "dgts_history.sqlite"))
        self.notice_kind_label = tk.StringVar(value="Tất cả")
        self.event_label = tk.StringVar(value="Tất cả")
        self.status = tk.StringVar(value="Sẵn sàng")
        self.rows: list[HistoryEventRow] = []
        self.page_index = 0

        self._build_layout()

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(6, weight=1)

        ctk.CTkLabel(self, text="Lịch sử thay đổi", font=(FONT_FAMILY, 20, "bold"), text_color=TEXT).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(4, 8)
        )
        filter_header = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=8, height=42)
        filter_header.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(16, 8))
        filter_header.grid_propagate(False)
        ctk.CTkLabel(
            filter_header, text="Nguồn dữ liệu & bộ lọc", font=(FONT_FAMILY, 13, "bold"), text_color=TEXT
        ).pack(side="left", padx=14, pady=10)

        ctk.CTkLabel(self, text="File DB lịch sử", font=(FONT_FAMILY, 11, "bold"), text_color=TEXT).grid(
            row=2, column=0, sticky="w", pady=7
        )
        ctk.CTkEntry(
            self, textvariable=self.history_db_path, height=32, fg_color=INPUT, border_color=BORDER, text_color=TEXT
        ).grid(row=2, column=1, columnspan=2, sticky="ew", pady=7)
        ctk.CTkButton(
            self,
            text="▣  Chọn DB",
            command=self._browse_history_db,
            width=126,
            height=32,
            fg_color="transparent",
            hover_color=SURFACE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
        ).grid(
            row=2, column=3, sticky="ew", padx=(12, 0), pady=7
        )

        ctk.CTkLabel(self, text="Loại tin", font=(FONT_FAMILY, 11, "bold"), text_color=TEXT).grid(
            row=3, column=0, sticky="w", pady=7
        )
        ctk.CTkComboBox(
            self,
            variable=self.notice_kind_label,
            values=list(NOTICE_KIND_OPTIONS),
            state="readonly",
            width=180,
            fg_color=INPUT,
            border_color=BORDER,
            text_color=TEXT,
            button_color=BORDER,
        ).grid(row=3, column=1, sticky="w", pady=7)
        ctk.CTkLabel(self, text="Sự kiện", font=(FONT_FAMILY, 11, "bold"), text_color=TEXT).grid(
            row=3, column=2, sticky="e", padx=(10, 0), pady=7
        )
        ctk.CTkComboBox(
            self,
            variable=self.event_label,
            values=list(EVENT_OPTIONS),
            state="readonly",
            width=190,
            fg_color=INPUT,
            border_color=BORDER,
            text_color=TEXT,
            button_color=BORDER,
        ).grid(row=3, column=3, sticky="w", padx=(10, 0), pady=7)

        actions = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10, border_width=1, border_color=BORDER)
        actions.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(14, 10))
        ctk.CTkButton(
            actions,
            text="↻  Tải lịch sử",
            command=self._reset_and_load_history,
            width=128,
            height=36,
            font=(FONT_FAMILY, 12, "bold"),
            fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        ).pack(side="left", padx=(12, 0), pady=10)
        for text, command in (("←  Trang trước", self._previous_page), ("Trang sau  →", self._next_page), ("⇩  Xuất Excel", self._export_excel)):
            ctk.CTkButton(
                actions,
                text=text,
                command=command,
                width=116,
                height=36,
                fg_color="transparent",
                hover_color=SURFACE,
                border_width=1,
                border_color=BORDER,
                text_color=TEXT,
            ).pack(side="left", padx=(10, 0), pady=10)
        ctk.CTkLabel(
            actions, textvariable=self.status, font=(FONT_FAMILY, 12, "bold"), text_color=TEXT
        ).pack(side="right", padx=14)

        # Style standard ttk Treeview to match light/dark appearance mode
        style = ttk.Style()
        mode = ctk.get_appearance_mode()
        style.theme_use("default")
        if mode == "Dark":
            style.configure(
                "Treeview",
                background="#252d30",
                foreground="white",
                fieldbackground="#252d30",
                borderwidth=0,
                font=(FONT_FAMILY, 10),
            )
            style.map("Treeview", background=[("selected", "#3a9a83")])
            style.configure(
                "Treeview.Heading",
                background="#30393d",
                foreground="white",
                borderwidth=0,
                font=(FONT_FAMILY, 10, "bold"),
            )
        else:
            style.configure(
                "Treeview",
                background="#ffffff",
                foreground="black",
                fieldbackground="#ffffff",
                borderwidth=0,
                font=(FONT_FAMILY, 10),
            )
            style.map("Treeview", background=[("selected", "#247a68")])
            style.configure(
                "Treeview.Heading",
                background="#eef2f0",
                foreground="black",
                borderwidth=0,
                font=(FONT_FAMILY, 10, "bold"),
            )

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
        self.table.grid(row=6, column=0, columnspan=4, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=6, column=4, sticky="ns")
        horizontal_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self.table.xview)
        horizontal_scrollbar.grid(row=7, column=0, columnspan=4, sticky="ew")
        self.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal_scrollbar.set)
        self.table.insert("", "end", values=("Chọn bộ lọc và nhấn “Tải lịch sử”",))

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


class CrawlerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DGTS Crawler")
        self.geometry("1180x900")
        self.minsize(980, 760)
        self.configure(fg_color=("#E7ECE9", "#181E21"))
        self.run_coordinator = RunCoordinator()
        self._build_layout()

    def _build_layout(self) -> None:
        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(16, 0))
        ctk.CTkLabel(
            brand, text="DGTS Crawler", font=(FONT_FAMILY, 24, "bold"), text_color=TEXT
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="Thu thập, lưu snapshot và theo dõi thay đổi dữ liệu đấu giá",
            font=(FONT_FAMILY, 11),
            text_color=MUTED,
        ).pack(anchor="w", pady=(1, 0))

        tabview = ctk.CTkTabview(
            self,
            anchor="nw",
            fg_color=PANEL,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=SURFACE,
            segmented_button_unselected_hover_color=BORDER,
            text_color=TEXT,
            corner_radius=12,
        )
        tabview.pack(fill="both", expand=True, padx=18, pady=(12, 18))

        for config in TAB_CONFIGS:
            tabview.add(config.tab_label)
        tabview.add(HISTORY_TAB_LABEL)

        for config in TAB_CONFIGS:
            tab_parent = tabview.tab(config.tab_label)
            scroll = ctk.CTkScrollableFrame(
                tab_parent,
                fg_color="transparent",
                scrollbar_button_color=BORDER,
                scrollbar_button_hover_color=ACCENT,
            )
            scroll.pack(fill="both", expand=True)
            tab = CrawlerTab(
                scroll,
                config.notice_kind,
                config.output_default,
                config.header,
                config.tab_label,
                self.run_coordinator,
            )
            tab.pack(fill="both", expand=True, padx=(2, 8), pady=(2, 10))

        history = HistoryTab(tabview.tab(HISTORY_TAB_LABEL))
        history.pack(fill="both", expand=True)


def main() -> None:
    app = CrawlerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
