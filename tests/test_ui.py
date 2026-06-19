from pathlib import Path

from dgts_crawler.history_store import HistoryEventRow
from dgts_crawler.ui import (
    CrawlerTab,
    DEFAULT_DETAIL_WORKERS,
    DEFAULT_UI_PAGE_SIZE,
    EVENT_OPTIONS,
    HISTORY_PAGE_SIZE,
    HISTORY_TAB_LABEL,
    NOTICE_KIND_OPTIONS,
    TAB_CONFIGS,
    RunCoordinator,
    _filter_option_labels,
    _focus_is_within_combo_suggestion,
    _option_map,
    _raise_suggestion_popup,
    _suggestion_popup_height,
    format_done_message,
    format_history_page_status,
    format_history_table_value,
)


def test_ui_uses_legacy_notebook_tab_labels():
    assert [config.tab_label for config in TAB_CONFIGS] == ["Tab 1", "Tab 2", "Tab 3"]
    assert HISTORY_TAB_LABEL == "Tab 4"
    assert NOTICE_KIND_OPTIONS["Tab 1 - Đấu giá"] == "auction"


def test_run_coordinator_allows_only_one_active_crawl():
    coordinator = RunCoordinator()

    assert coordinator.try_start("Tab 1") is True
    assert coordinator.try_start("Tab 2") is False
    assert coordinator.active_label == "Tab 1"

    coordinator.finish("Tab 2")
    assert coordinator.active_label == "Tab 1"

    coordinator.finish("Tab 1")
    assert coordinator.try_start("Tab 2") is True


def test_ui_defaults_are_tuned_for_faster_crawls():
    assert DEFAULT_UI_PAGE_SIZE == "10"
    assert DEFAULT_DETAIL_WORKERS == "5"


def test_ui_done_message_does_not_repeat_ambiguous_count(tmp_path):
    assert format_done_message(tmp_path / "out.xlsx") == f"Hoàn tất. File: {tmp_path / 'out.xlsx'}"


def test_history_page_status_includes_page_number_and_row_count():
    assert HISTORY_PAGE_SIZE == 500
    assert format_history_page_status(page_index=0, row_count=500) == "Trang 1 - 500 dòng"
    assert format_history_page_status(page_index=2, row_count=120) == "Trang 3 - 120 dòng"


def test_history_event_options_include_same_asset_name():
    assert EVENT_OPTIONS["SAME_ASSET_NAME"] == "SAME_ASSET_NAME"


def test_history_table_text_is_single_line_for_treeview():
    row = HistoryEventRow(
        created_at="2026-06-06T22:03:40",
        notice_kind="select-org-result",
        event_type="SUSPECT_REPOST",
        notice_id="28036",
        publish_date="01/06/2026",
        detail_url="https://dgts.moj.gov.vn/tin-28036.html",
        changed_fields="detail_url, match_type, notice_id, publish_date",
        changed_details=(
            "Tin cũ: 28036\n"
            "Tin mới: 28037\n"
            "Kiểu khớp: exact_asset_fingerprint\n"
            "Ngày đăng cũ: 01/06/2026\n"
            "Ngày đăng mới: 01/06/2026"
        ),
        old_values="detail_url: https://dgts.moj.gov.vn/tin-28036.html\nnotice_id: 28036",
        new_values="detail_url: https://dgts.moj.gov.vn/tin-28037.html\nnotice_id: 28037",
        matched_notice_id="28037",
    )

    assert format_history_table_value(row, "changed_details") == "Cũ 28036 -> Mới 28037 (01/06/2026 -> 01/06/2026)"
    assert "\n" not in format_history_table_value(row, "old_values")
    assert format_history_table_value(row, "changed_fields") == "notice_id, publish_date"


def test_option_map_uses_id_and_requested_label_field():
    options = _option_map(
        [
            {"id": 2063, "fullname": "Tổ chức A"},
            {"id": 2064, "fullname": "Tổ chức B"},
            {"id": "", "fullname": "Thiếu id"},
            {"id": 2065, "fullname": ""},
        ],
        "fullname",
    )

    assert options == {"Tất cả": "", "Tổ chức A": "2063", "Tổ chức B": "2064"}


def test_filter_option_labels_keeps_all_label_and_matches_typed_text():
    labels = ["Tất cả", "Hà Nội", "Hải Phòng", "Đà Nẵng", "Cần Thơ"]

    assert _filter_option_labels(labels, "h") == ["Tất cả", "Hà Nội", "Hải Phòng", "Cần Thơ"]
    assert _filter_option_labels(labels, "hai") == ["Tất cả", "Hải Phòng"]
    assert _filter_option_labels(labels, "da") == ["Tất cả", "Đà Nẵng"]
    assert _filter_option_labels(labels, "   ") == labels


def test_suggestion_popup_height_is_capped_but_never_empty():
    assert _suggestion_popup_height(0) == 1
    assert _suggestion_popup_height(3) == 3
    assert _suggestion_popup_height(99) == 10


def test_focus_inside_combo_entry_counts_as_suggestion_context():
    class Popup:
        pass

    class Root:
        pass

    popup = Popup()
    root = Root()

    class Focus:
        def __init__(self, toplevel):
            self.toplevel = toplevel

        def winfo_toplevel(self):
            return self.toplevel

    class Combo:
        pass

    combo = Combo()
    combo._entry = Focus(root)

    assert _focus_is_within_combo_suggestion(combo._entry, combo, popup) is True
    assert _focus_is_within_combo_suggestion(Focus(popup), combo, popup) is True
    assert _focus_is_within_combo_suggestion(Focus(root), combo, popup) is False


def test_raise_suggestion_popup_lifts_popup_without_keeping_topmost():
    calls = []

    class Popup:
        def lift(self):
            calls.append("lift")

        def attributes(self, key, value):
            calls.append((key, value))

        def after(self, _delay, callback):
            calls.append("after")
            callback()

    _raise_suggestion_popup(Popup())

    assert calls == ["lift", ("-topmost", True), "after", ("-topmost", False)]


def test_tab_1_output_file_is_auction_excel():
    assert TAB_CONFIGS[0].notice_kind == "auction"
    assert TAB_CONFIGS[0].output_default == Path("outputs") / "dgts_auction_notices.xlsx"


def test_auction_config_uses_public_date_range_for_logged_crawl_dates():
    tab = object.__new__(CrawlerTab)
    tab.notice_kind = "auction"
    tab.from_date = _value("01/01/2000")
    tab.to_date = _value("02/01/2000")
    tab.auction_start_publish_date = _value("31/05/2026")
    tab.auction_end_publish_date = _value("07/06/2026")
    tab.max_pages = _value("10")
    tab.page_size = _value("100")
    tab.detail_workers = _value("5")
    tab.crawl_all = _value(False)
    tab.output_path = _value("outputs/out.xlsx")
    tab.history_db_path = _value("outputs/history.sqlite")
    tab.enable_history = _value(True)
    tab.enable_screenshots = _value(False)
    tab.screenshot_dir = _value("outputs/screenshots")
    tab._read_auction_filters = lambda: "auction-filters"

    config = CrawlerTab._read_config(tab)

    assert config.from_date == "31/05/2026"
    assert config.to_date == "07/06/2026"
    assert config.auction_filters == "auction-filters"


def test_tab_config_reads_screenshot_options(tmp_path):
    tab = object.__new__(CrawlerTab)
    tab.notice_kind = "auction"
    tab.from_date = _value("01/01/2000")
    tab.to_date = _value("02/01/2000")
    tab.auction_start_publish_date = _value("31/05/2026")
    tab.auction_end_publish_date = _value("07/06/2026")
    tab.max_pages = _value("10")
    tab.page_size = _value("100")
    tab.detail_workers = _value("5")
    tab.crawl_all = _value(False)
    tab.output_path = _value("outputs/out.xlsx")
    tab.history_db_path = _value("outputs/history.sqlite")
    tab.enable_history = _value(True)
    tab.enable_screenshots = _value(True)
    tab.screenshot_dir = _value(str(tmp_path / "shots"))
    tab._read_auction_filters = lambda: "auction-filters"

    config = CrawlerTab._read_config(tab)

    assert config.enable_screenshots is True
    assert config.screenshot_dir == tmp_path / "shots"


class _value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value
