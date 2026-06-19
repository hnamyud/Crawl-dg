from datetime import datetime
from pathlib import Path

import pytest

from dgts_crawler.history_store import HistorySnapshot
from dgts_crawler.runner import AuctionFilters, CrawlerConfig, format_date_arg, resolve_dates, run_crawl, validate_config


def _history_counts(**overrides):
    counts = {
        "NEW": 1,
        "CHANGED": 0,
        "MISSING": 0,
        "REAPPEARED": 0,
        "SUSPECT_REPOST": 0,
        "SAME_ASSET_NAME": 0,
    }
    counts.update(overrides)
    return counts


def test_default_page_size_is_10_like_the_website():
    assert CrawlerConfig().page_size == 10
    assert CrawlerConfig().notice_kind == "auction"
    assert CrawlerConfig().detail_workers == 1


def test_format_date_arg_accepts_iso_and_vietnamese_dates():
    assert format_date_arg("2026-06-05") == "05/06/2026"
    assert format_date_arg("05/06/2026") == "05/06/2026"


def test_resolve_dates_defaults_to_last_seven_days_when_not_crawling_all():
    config = CrawlerConfig()
    today = datetime(2026, 6, 5)

    assert resolve_dates(config, today=today) == ("29/05/2026", "05/06/2026")


def test_resolve_dates_keeps_blank_dates_when_crawling_all():
    config = CrawlerConfig(crawl_all=True)

    assert resolve_dates(config, today=datetime(2026, 6, 5)) == ("", "")


def test_validate_config_rejects_invalid_page_settings(tmp_path):
    config = CrawlerConfig(
        max_pages=0,
        page_size=0,
        detail_workers=0,
        output_path=tmp_path / "out.xlsx",
    )

    errors = validate_config(config)

    assert "Số trang tối đa phải lớn hơn 0." in errors
    assert "Số bản ghi mỗi trang phải lớn hơn 0." in errors
    assert "Số luồng tải chi tiết phải lớn hơn 0." in errors


def test_validate_config_rejects_unknown_notice_kind(tmp_path):
    config = CrawlerConfig(notice_kind="unknown", output_path=tmp_path / "out.xlsx")

    assert "Loại thông báo không hợp lệ." in validate_config(config)


def test_validate_config_accepts_valid_config(tmp_path):
    config = CrawlerConfig(
        from_date="2026-06-01",
        to_date="2026-06-05",
        max_pages=1,
        page_size=10,
        output_path=Path("outputs") / "out.xlsx",
    )

    assert validate_config(config) == []


def test_run_crawl_records_auction_history_snapshots(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"
    history_db = tmp_path / "history.sqlite"
    calls = {}

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {
                "id": 1,
                "propertyName": "Tài sản A",
                "titleName": "Đấu giá ",
                "fullname": "Cơ quan A",
                "publishTime1": 1780638042132,
                "aucRegTimeEnd": 1781024400000,
            }

        def property_detail(self, notice_id):
            return {
                "propertyStartPrice": 1000,
                "deposit": 100,
                "depositUnit": 0,
                "propertyPlace": "Ngõ 83 đường Đình Xuyên, tổ dân phố Yên Bình, xã Phù Đổng, TP Hà Nội",
            }

    class FakeHistoryStore:
        def __init__(self, db_path):
            calls["db_path"] = db_path

        def record_crawl(
            self,
            notice_kind,
            start_date,
            end_date,
            snapshots,
            detect_missing=True,
            missing_exists_validator=None,
        ):
            calls["notice_kind"] = notice_kind
            calls["start_date"] = start_date
            calls["end_date"] = end_date
            calls["snapshots"] = list(snapshots)
            calls["detect_missing"] = detect_missing
            calls["missing_exists_validator"] = missing_exists_validator
            return type("Result", (), {"event_counts": _history_counts()})()

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", lambda output_path, rows: output_path)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", FakeHistoryStore)

    run_crawl(
        CrawlerConfig(
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
            history_db_path=history_db,
        ),
        client=FakeClient(),
    )

    assert calls["db_path"] == history_db
    assert calls["notice_kind"] == "auction"
    assert calls["start_date"] == "05/06/2026"
    assert calls["detect_missing"] is True
    assert calls["missing_exists_validator"](HistorySnapshot(
        notice_kind="auction",
        notice_id="2",
        publish_date="05/06/2026",
        detail_url="https://dgts.moj.gov.vn/tin-2.html",
        tracked_fields={},
        raw_payload={},
    )) is True
    assert len(calls["snapshots"]) == 1
    snapshot = calls["snapshots"][0]
    assert snapshot.notice_id == "1"
    assert snapshot.tracked_fields["asset_name"] == "Tài sản A"
    assert snapshot.tracked_fields["owner_name"] == "Cơ quan A"
    assert snapshot.tracked_fields["start_price"] == 1000
    assert snapshot.tracked_fields["province"] == "Hà Nội"
    assert snapshot.tracked_fields["property_place"] == (
        "Ngõ 83 đường Đình Xuyên, tổ dân phố Yên Bình, xã Phù Đổng, TP Hà Nội"
    )


def test_run_crawl_select_org_history_validator_checks_detail(monkeypatch, tmp_path):
    output = tmp_path / "select-org.xlsx"
    calls = {}
    detail_calls = []

    class FakeClient:
        def iter_select_org_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Tài sản A", "lastUpdated": 1780664230000}

        def select_org_detail(self, notice_id):
            detail_calls.append(("detail", notice_id))
            return {"items": [{"id": notice_id, "propertyName": "Tài sản A", "genDate": 1780657028537}]}

        def select_org_property_info(self, notice_id):
            return [{"propertyName": "Tài sản A", "propertyStartPrice": 1000}]

    class FakeHistoryStore:
        def __init__(self, db_path):
            pass

        def record_crawl(
            self,
            notice_kind,
            start_date,
            end_date,
            snapshots,
            detect_missing=True,
            missing_exists_validator=None,
        ):
            calls["missing_exists_validator"] = missing_exists_validator
            return type("Result", (), {"event_counts": _history_counts()})()

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_workbook", lambda output_path, rows: output_path)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", FakeHistoryStore)

    run_crawl(
        CrawlerConfig(
            notice_kind="select-org",
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
        ),
        client=FakeClient(),
    )

    assert calls["missing_exists_validator"](HistorySnapshot(
        notice_kind="select-org",
        notice_id="2",
        publish_date="05/06/2026",
        detail_url="https://dgts.moj.gov.vn/tin-2.html",
        tracked_fields={},
        raw_payload={},
    )) is True
    assert ("detail", "2") in detail_calls


def test_run_crawl_select_org_result_history_validator_checks_result_endpoints(monkeypatch, tmp_path):
    output = tmp_path / "select-org-result.xlsx"
    calls = {}
    info_calls = []

    class FakeClient:
        def iter_select_org_result_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Tài sản A", "publishTime": 1780663701000}

        def select_org_result_owner(self, result_id):
            return {"fullname": "Cơ quan A"}

        def select_org_result_history(self, result_id):
            return {"orgInfo": {"orgName": "Tổ chức A"}, "property": [{"propertyName": "Tài sản A"}]}

        def select_org_result_info(self, result_id):
            info_calls.append(("info", result_id))
            return {"publishTime": 1780663701000, "subPropertyName": "Tài sản A"}

    class FakeHistoryStore:
        def __init__(self, db_path):
            pass

        def record_crawl(
            self,
            notice_kind,
            start_date,
            end_date,
            snapshots,
            detect_missing=True,
            missing_exists_validator=None,
        ):
            calls["missing_exists_validator"] = missing_exists_validator
            return type("Result", (), {"event_counts": _history_counts()})()

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_result_workbook", lambda output_path, rows: output_path)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", FakeHistoryStore)

    run_crawl(
        CrawlerConfig(
            notice_kind="select-org-result",
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
        ),
        client=FakeClient(),
    )

    assert calls["missing_exists_validator"](HistorySnapshot(
        notice_kind="select-org-result",
        notice_id="2",
        publish_date="05/06/2026",
        detail_url="https://dgts.moj.gov.vn/tin-2.html",
        tracked_fields={},
        raw_payload={},
    )) is True
    assert ("info", "2") in info_calls


def test_run_crawl_history_validator_logs_and_skips_missing_on_detail_error(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"
    messages = []
    calls = {}

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Tài sản A", "publishTime1": 1780638042132}

        def property_detail(self, notice_id):
            if str(notice_id) == "missing":
                raise RuntimeError("network failed")
            return {"propertyStartPrice": 1000, "deposit": 100, "depositUnit": 0}

    class FakeHistoryStore:
        def __init__(self, db_path):
            pass

        def record_crawl(
            self,
            notice_kind,
            start_date,
            end_date,
            snapshots,
            detect_missing=True,
            missing_exists_validator=None,
        ):
            calls["validator_result"] = missing_exists_validator(HistorySnapshot(
                notice_kind="auction",
                notice_id="missing",
                publish_date="05/06/2026",
                detail_url="https://dgts.moj.gov.vn/tin-missing.html",
                tracked_fields={},
                raw_payload={},
            ))
            return type("Result", (), {"event_counts": _history_counts()})()

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", lambda output_path, rows: output_path)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", FakeHistoryStore)

    run_crawl(
        CrawlerConfig(
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
        ),
        client=FakeClient(),
        progress=messages.append,
    )

    assert calls["validator_result"] is True
    assert any("Bỏ qua MISSING" in message and "missing" in message for message in messages)


def test_run_crawl_skips_history_when_disabled(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Tài sản A", "publishTime1": 1780638042132}

        def property_detail(self, notice_id):
            return {"propertyStartPrice": 1000, "deposit": 100, "depositUnit": 0}

    def fail_history_store(db_path):
        raise AssertionError("history store should not be created")

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", lambda output_path, rows: output_path)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", fail_history_store)

    run_crawl(CrawlerConfig(output_path=output, enable_history=False), client=FakeClient())


def test_run_crawl_emits_progress_for_each_checked_notice(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"
    messages = []

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Tài sản A", "publishTime1": 1780638042132}

        def property_detail(self, notice_id):
            return {"propertyStartPrice": 1000, "deposit": 100, "depositUnit": 0}

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", lambda output_path, rows: output_path)

    run_crawl(
        CrawlerConfig(output_path=output, enable_history=False),
        client=FakeClient(),
        progress=messages.append,
    )

    assert "Đã kiểm tra 1 tin..." in messages
    assert f"Hoàn tất: đã kiểm tra 1 tin, xuất 1 dòng -> {output}" in messages


def test_run_crawl_select_org_reports_checked_and_exported_counts_separately(monkeypatch, tmp_path):
    output = tmp_path / "select-org-filtered.xlsx"
    messages = []

    class FakeClient:
        def iter_select_org_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Trong ngày", "lastUpdated": 1780664230000}
            yield {"id": 2, "propertyName": "Ngoài ngày", "lastUpdated": 1780540000000}

        def select_org_detail(self, notice_id):
            gen_date = 1780657028537 if notice_id == 1 else 1780540000000
            return {"items": [{"id": notice_id, "propertyName": "Tài sản", "genDate": gen_date}]}

        def select_org_property_info(self, notice_id):
            return [{"propertyName": f"Tài sản {notice_id}", "propertyStartPrice": 1000}]

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_workbook", lambda output_path, rows: output_path)

    run_crawl(
        CrawlerConfig(
            notice_kind="select-org",
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
            enable_history=False,
        ),
        client=FakeClient(),
        progress=messages.append,
    )

    assert f"Hoàn tất: đã kiểm tra 2 tin, xuất 1 dòng -> {output}" in messages


def test_run_crawl_disables_missing_detection_when_stopped_early(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"
    history_db = tmp_path / "history.sqlite"
    detail_calls = []
    calls = {}

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Tài sản A", "publishTime1": 1780638042132}
            yield {"id": 2, "propertyName": "Tài sản B", "publishTime1": 1780638042132}

        def property_detail(self, notice_id):
            detail_calls.append(notice_id)
            return {"propertyStartPrice": 1000, "deposit": 100, "depositUnit": 0}

    class FakeHistoryStore:
        def __init__(self, db_path):
            pass

        def record_crawl(
            self,
            notice_kind,
            start_date,
            end_date,
            snapshots,
            detect_missing=True,
            missing_exists_validator=None,
        ):
            calls["detect_missing"] = detect_missing
            calls["snapshots"] = list(snapshots)
            return type("Result", (), {"event_counts": _history_counts()})()

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", lambda output_path, rows: output_path)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", FakeHistoryStore)

    run_crawl(
        CrawlerConfig(output_path=output, history_db_path=history_db),
        client=FakeClient(),
        should_stop=lambda: len(detail_calls) >= 1,
    )

    assert calls["detect_missing"] is False
    assert len(calls["snapshots"]) == 1


def test_run_crawl_can_stop_early_and_still_export_partial_rows(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"
    detail_calls = []
    exported = {}

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Thanh lý xe ô tô", "publishTime1": 1780638042132}
            yield {"id": 2, "propertyName": "Quyền sử dụng đất", "publishTime1": 1780638042132}

        def property_detail(self, notice_id):
            detail_calls.append(notice_id)
            return {"propertyStartPrice": 1000, "deposit": 100, "depositUnit": 0}

    def fake_write_workbook(output_path, rows):
        exported["output_path"] = output_path
        exported["rows"] = rows
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", fake_write_workbook)

    result, count = run_crawl(
        CrawlerConfig(output_path=output, enable_history=False),
        client=FakeClient(),
        should_stop=lambda: len(detail_calls) >= 1,
    )

    assert result == output
    assert count == 1
    assert detail_calls == [1]
    assert len(exported["rows"]) == 1


def test_run_crawl_passes_auction_filters_and_exports_multiple_property_rows(monkeypatch, tmp_path):
    output = tmp_path / "out.xlsx"
    exported = {}
    calls = {}

    class FakeClient:
        def iter_notices(self, **kwargs):
            calls["iter_kwargs"] = kwargs
            yield {
                "id": 1,
                "propertyName": "Tài sản gốc",
                "publishTime1": 1780638042132,
                "fullname": "Cơ quan A",
                "orgName": "Tổ chức A",
            }

        def property_detail(self, notice_id):
            return {
                "items": [
                    {"propertyName": "Xe 1", "propertyAmount": "01", "propertyStartPrice": 1000, "deposit": 100},
                    {"propertyName": "Xe 2", "propertyAmount": "02", "propertyStartPrice": 2000, "deposit": 200},
                ]
            }

    class FakeHistoryStore:
        def __init__(self, db_path):
            pass

        def record_crawl(
            self,
            notice_kind,
            start_date,
            end_date,
            snapshots,
            detect_missing=True,
            missing_exists_validator=None,
        ):
            calls["snapshots"] = list(snapshots)
            return type("Result", (), {"event_counts": _history_counts()})()

    def fake_write_workbook(output_path, rows):
        exported["rows"] = list(rows)
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", fake_write_workbook)
    monkeypatch.setattr("dgts_crawler.runner.HistoryStore", FakeHistoryStore)

    filters = AuctionFilters(
        province_id="109849",
        district_id="110197",
        start_publish_date="01/06/2026",
        end_publish_date="05/06/2026",
    )
    result, count = run_crawl(
        CrawlerConfig(output_path=output, history_db_path=tmp_path / "history.sqlite", auction_filters=filters),
        client=FakeClient(),
    )

    assert result == output
    assert count == 2
    assert calls["iter_kwargs"]["filters"] == filters
    assert [row.values[6] for row in exported["rows"]] == ["Xe 1", "Xe 2"]
    assert len(calls["snapshots"]) == 1


def test_run_crawl_select_org_fetches_detail_property_info_and_exports_partial_rows(monkeypatch, tmp_path):
    output = tmp_path / "select-org.xlsx"
    detail_calls = []
    property_calls = []
    exported = {}

    class FakeClient:
        def iter_select_org_notices(self, **kwargs):
            yield {"id": 105862, "propertyName": "Quyền sử dụng đất", "lastUpdated": 1780664230000}
            yield {"id": 105861, "propertyName": "Xe ô tô", "lastUpdated": 1780663190000}

        def select_org_detail(self, notice_id):
            detail_calls.append(notice_id)
            return {
                "items": [
                    {
                        "ownerFullname": "Cơ quan A",
                        "addrOwner": "Địa chỉ A",
                        "genDate": 1780657028537,
                        "fromDate": 1780851600000,
                        "toDate": 1781024400000,
                        "addressReceive": "Nơi nhận",
                        "contactInfo": "Liên hệ A",
                    }
                ]
            }

        def select_org_property_info(self, notice_id):
            property_calls.append(notice_id)
            return [{"propertyName": "Tài sản A", "propertyStartPrice": 1000}]

    def fake_write_select_org_workbook(output_path, rows):
        exported["output_path"] = output_path
        exported["rows"] = rows
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_workbook", fake_write_select_org_workbook)

    result, count = run_crawl(
        CrawlerConfig(notice_kind="select-org", output_path=output, enable_history=False),
        client=FakeClient(),
        should_stop=lambda: len(detail_calls) >= 1,
    )

    assert result == output
    assert count == 1
    assert detail_calls == [105862]
    assert property_calls == [105862]
    assert exported["output_path"] == output
    assert len(exported["rows"]) == 1


def test_run_crawl_select_org_filters_publish_date_locally_after_detail_fetch(monkeypatch, tmp_path):
    output = tmp_path / "select-org-filtered.xlsx"
    detail_calls = []
    exported = {}

    class FakeClient:
        def iter_select_org_notices(self, **kwargs):
            assert kwargs["start_date"] == "05/06/2026"
            assert kwargs["end_date"] == "05/06/2026"
            yield {"id": 1, "propertyName": "Trong ngày", "lastUpdated": 1780664230000}
            yield {"id": 2, "propertyName": "Ngoài ngày", "lastUpdated": 1780540000000}

        def select_org_detail(self, notice_id):
            detail_calls.append(notice_id)
            gen_date = 1780657028537 if notice_id == 1 else 1780540000000
            return {
                "items": [
                    {
                        "id": notice_id,
                        "propertyName": "Tài sản",
                        "ownerFullname": "Cơ quan",
                        "genDate": gen_date,
                    }
                ]
            }

        def select_org_property_info(self, notice_id):
            return [{"propertyName": f"Tài sản {notice_id}", "propertyStartPrice": 1000}]

    def fake_write_select_org_workbook(output_path, rows):
        exported["rows"] = rows
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_workbook", fake_write_select_org_workbook)

    result, count = run_crawl(
        CrawlerConfig(
            notice_kind="select-org",
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
            enable_history=False,
        ),
        client=FakeClient(),
    )

    assert result == output
    assert count == 1
    assert detail_calls == [1, 2]
    assert len(exported["rows"]) == 1
    assert exported["rows"][0].values[2] == "Tài sản 1"


def test_run_crawl_select_org_exports_one_row_per_property_row(monkeypatch, tmp_path):
    output = tmp_path / "select-org-multiple-properties.xlsx"
    exported = {}

    class FakeClient:
        def iter_select_org_notices(self, **kwargs):
            yield {"id": 1, "propertyName": "Xe ô tô", "lastUpdated": 1780664230000}

        def select_org_detail(self, notice_id):
            return {
                "items": [
                    {
                        "id": notice_id,
                        "propertyName": "Xe ô tô",
                        "ownerFullname": "Cơ quan A",
                        "genDate": 1780657028537,
                    }
                ]
            }

        def select_org_property_info(self, notice_id):
            return [
                {"propertyName": "Xe 1", "propertyStartPrice": 35000000},
                {"propertyName": "Xe 2", "propertyStartPrice": 85000000},
                {"propertyName": "Xe 3", "propertyStartPrice": 100000000},
            ]

    def fake_write_select_org_workbook(output_path, rows):
        exported["rows"] = rows
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_workbook", fake_write_select_org_workbook)

    result, count = run_crawl(
        CrawlerConfig(
            notice_kind="select-org",
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
            enable_history=False,
        ),
        client=FakeClient(),
    )

    assert result == output
    assert count == 3
    assert [row.values[2] for row in exported["rows"]] == ["Xe 1", "Xe 2", "Xe 3"]
    assert [row.values[7] for row in exported["rows"]] == [35000000, 85000000, 100000000]


def test_run_crawl_captures_screenshots_for_each_normalized_row(monkeypatch, tmp_path):
    output = tmp_path / "auction.xlsx"
    screenshot_dir = tmp_path / "shots"
    captured = {}

    class FakeClient:
        def iter_notices(self, **kwargs):
            yield {"id": 105862, "propertyName": "Xe ô tô", "publicDate": 1780664230000}

        def auction_detail(self, notice):
            return {
                "items": [
                    {"propertyName": "Xe 1", "propertyStartPrice": 1000},
                    {"propertyName": "Xe 2", "propertyStartPrice": 2000},
                ]
            }

    def fake_write_workbook(output_path, rows):
        return output_path

    def fake_capture_screenshot_jobs(jobs, output_dir, progress=None):
        captured["jobs"] = list(jobs)
        captured["output_dir"] = output_dir
        return None

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", fake_write_workbook)
    monkeypatch.setattr("dgts_crawler.runner.capture_screenshot_jobs", fake_capture_screenshot_jobs)

    result, count = run_crawl(
        CrawlerConfig(
            output_path=output,
            enable_history=False,
            enable_screenshots=True,
            screenshot_dir=screenshot_dir,
            screenshot_started_at=datetime(2026, 6, 19, 16, 30, 5),
        ),
        client=FakeClient(),
    )

    assert result == output
    assert count == 2
    assert captured["output_dir"] == screenshot_dir / "auction_2026-06-19_16-30-05"
    assert [(job.notice_kind, job.notice_id, job.asset_index) for job in captured["jobs"]] == [
        ("auction", "105862", 1),
        ("auction", "105862", 2),
    ]
    assert [job.sequence for job in captured["jobs"]] == [1, 2]
    assert all(job.url.endswith("-105862.html") for job in captured["jobs"])


def test_run_crawl_logs_when_screenshot_enabled_but_no_rows_match(monkeypatch, tmp_path):
    output = tmp_path / "auction.xlsx"
    messages = []

    class FakeClient:
        def iter_notices(self, **kwargs):
            return iter(())

    def fake_write_workbook(output_path, rows):
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_workbook", fake_write_workbook)

    run_crawl(
        CrawlerConfig(
            output_path=output,
            enable_history=False,
            enable_screenshots=True,
            screenshot_dir=tmp_path / "shots",
            screenshot_started_at=datetime(2026, 6, 19, 16, 30, 5),
        ),
        client=FakeClient(),
        progress=messages.append,
    )

    assert f"Thư mục lưu ảnh: {tmp_path / 'shots' / 'auction_2026-06-19_16-30-05'}" in messages
    assert "Không có bài nào để chụp ảnh." in messages


def test_run_crawl_select_org_result_filters_locally_and_exports_property_rows(monkeypatch, tmp_path):
    output = tmp_path / "select-org-result.xlsx"
    exported = {}
    owner_calls = []
    history_calls = []
    info_calls = []

    class FakeClient:
        def iter_select_org_result_notices(self, **kwargs):
            assert kwargs["start_date"] == "05/06/2026"
            assert kwargs["end_date"] == "05/06/2026"
            yield {"id": 1, "propertyName": "Trong ngày", "publishTime": 1780663701000, "orgName": "Tổ chức A"}
            yield {"id": 2, "propertyName": "Ngoài ngày", "publishTime": 1780540000000, "orgName": "Tổ chức B"}

        def select_org_result_owner(self, result_id):
            owner_calls.append(result_id)
            return {"fullname": f"Cơ quan {result_id}", "addrOwner": f"Địa chỉ cơ quan {result_id}"}

        def select_org_result_history(self, result_id):
            history_calls.append(result_id)
            return {
                "orgInfo": {
                    "orgName": f"Tổ chức {result_id}",
                    "addrFull": f"Địa chỉ tổ chức {result_id}",
                    "foneNumber": f"090000000{result_id}",
                },
                "property": [
                    {"propertyName": f"Tài sản {result_id}A", "propertyStartPrice": 1000},
                    {"propertyName": f"Tài sản {result_id}B", "propertyStartPrice": 2000},
                ],
            }

        def select_org_result_info(self, result_id):
            info_calls.append(result_id)
            publish_time = 1780663701000 if result_id == 1 else 1780540000000
            return {"publishTime": publish_time, "subPropertyName": f"Thông báo {result_id}"}

    def fake_write_select_org_result_workbook(output_path, rows):
        exported["rows"] = rows
        return output_path

    monkeypatch.setattr("dgts_crawler.runner.write_select_org_result_workbook", fake_write_select_org_result_workbook)

    result, count = run_crawl(
        CrawlerConfig(
            notice_kind="select-org-result",
            from_date="05/06/2026",
            to_date="05/06/2026",
            output_path=output,
            enable_history=False,
        ),
        client=FakeClient(),
    )

    assert result == output
    assert count == 2
    assert owner_calls == [1, 2]
    assert history_calls == [1, 2]
    assert info_calls == [1, 2]
    assert [row.values[2] for row in exported["rows"]] == ["Tài sản 1A", "Tài sản 1B"]
    assert [row.values[7] for row in exported["rows"]] == [1000, 2000]
