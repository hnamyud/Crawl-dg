from dgts_crawler.history_store import HistorySnapshot, HistoryStore


def _snapshot(notice_id="1", publish_date="05/06/2026", name=None):
    asset_name = name if name is not None else f"Tài sản {notice_id}"
    return HistorySnapshot(
        notice_kind="auction",
        notice_id=str(notice_id),
        publish_date=publish_date,
        detail_url=f"https://dgts.moj.gov.vn/tin-{notice_id}.html",
        tracked_fields={
            "publish_date": publish_date,
            "asset_name": asset_name,
            "owner_name": "Cơ quan A",
            "start_price": 1000,
        },
        raw_payload={"id": notice_id, "propertyName": asset_name},
    )


def _with_detail_url(snapshot, detail_url):
    tracked_fields = dict(snapshot.tracked_fields)
    tracked_fields["detail_url"] = detail_url
    return HistorySnapshot(
        notice_kind=snapshot.notice_kind,
        notice_id=snapshot.notice_id,
        publish_date=snapshot.publish_date,
        detail_url=detail_url,
        tracked_fields=tracked_fields,
        raw_payload=snapshot.raw_payload,
    )


def _auction_snapshot_with_place(notice_id, publish_date, place, deadline):
    return HistorySnapshot(
        notice_kind="auction",
        notice_id=str(notice_id),
        publish_date=publish_date,
        detail_url=f"https://dgts.moj.gov.vn/tin-{notice_id}.html",
        tracked_fields={
            "publish_date": publish_date,
            "notice_code": f"TS_{notice_id}",
            "asset_name": "Thông báo việc đấu giá đối với danh mục tài sản: Tài sản là quyền sử dụng đất theo quy định của pháp luật về đất đai;",
            "province": "Toàn quốc",
            "owner_name": "Trung tâm phát triển quỹ đất tỉnh Lai Châu",
            "start_price": 0,
            "deposit": 0,
            "deadline": deadline,
            "detail_url": f"https://dgts.moj.gov.vn/tin-{notice_id}.html",
            "property_place": place,
            "group": "Đất đai",
        },
        raw_payload={"id": notice_id},
    )


def _auction_snapshot_without_place(notice_id, publish_date, deadline):
    snapshot = _auction_snapshot_with_place(notice_id, publish_date, "", deadline)
    tracked_fields = dict(snapshot.tracked_fields)
    tracked_fields.pop("property_place", None)
    return HistorySnapshot(
        notice_kind=snapshot.notice_kind,
        notice_id=snapshot.notice_id,
        publish_date=snapshot.publish_date,
        detail_url=snapshot.detail_url,
        tracked_fields=tracked_fields,
        raw_payload=snapshot.raw_payload,
    )


def _counts(**overrides):
    counts = {
        "NEW": 0,
        "CHANGED": 0,
        "MISSING": 0,
        "REAPPEARED": 0,
        "SUSPECT_REPOST": 0,
        "SAME_ASSET_NAME": 0,
    }
    counts.update(overrides)
    return counts


def test_history_store_records_new_changed_missing_and_reappeared(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)

    first = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot()],
    )

    assert first.event_counts == _counts(NEW=1)
    assert store.history_events() == [("NEW", "1")]

    unchanged = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot()],
    )

    assert unchanged.event_counts == _counts()
    assert store.history_events() == [("NEW", "1")]

    changed_snapshot = _snapshot()
    changed_snapshot.tracked_fields["deadline"] = "12/06/2026"
    changed = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[changed_snapshot],
    )

    assert changed.event_counts == _counts(CHANGED=1)
    assert store.history_events()[-1] == ("CHANGED", "1")

    missing = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[],
        missing_exists_validator=lambda snapshot: False,
    )

    assert missing.event_counts == _counts(MISSING=1)
    assert store.history_events()[-1] == ("MISSING", "1")

    reappeared = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[changed_snapshot],
    )

    assert reappeared.event_counts == _counts(REAPPEARED=1)
    assert store.history_events()[-1] == ("REAPPEARED", "1")


def test_history_store_does_not_mark_changed_when_only_group_changes(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot()
    first.tracked_fields["group"] = "Đất đai"
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[first],
    )

    second = _snapshot()
    second.tracked_fields["group"] = "Thi hành án"
    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[second],
    )

    assert result.event_counts == _counts()
    assert store.history_events() == [("NEW", "1")]


def test_history_store_does_not_mark_changed_when_only_asset_name_and_url_change(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 1: Tài sản A,Lô 2: Tài sản B",
    )
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[first],
    )

    second = _snapshot(
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 2: Tài sản B,Lô 1: Tài sản A",
    )
    second = _with_detail_url(second, "https://dgts.moj.gov.vn/tin-doi-slug-1.html")
    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[second],
    )

    assert result.event_counts == _counts()
    assert store.history_events() == [("NEW", "1")]


def test_history_store_excludes_asset_name_and_url_from_changed_fields(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(name="Tài sản A")
    first.tracked_fields["deadline"] = "10/06/2026"
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[first],
    )

    second = _snapshot(name="Tài sản B")
    second = _with_detail_url(second, "https://dgts.moj.gov.vn/tin-doi-slug-1.html")
    second.tracked_fields["deadline"] = "12/06/2026"
    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[second],
    )

    assert result.event_counts == _counts(CHANGED=1)
    rows = store.list_history_rows(event_type="CHANGED")
    assert rows[0].changed_fields == "deadline"
    assert rows[0].old_values == "deadline: 10/06/2026"
    assert rows[0].new_values == "deadline: 12/06/2026"


def test_history_store_does_not_mark_changed_when_only_province_mapping_changes(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot()
    first.tracked_fields["province"] = "Hà Nội"
    first.tracked_fields["property_place"] = "Ngõ 83 đường Đình Xuyên, TP Hà Nội"
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[first],
    )

    second = _snapshot()
    second.tracked_fields["province"] = "Ngõ 83 đường Đình Xuyên, TP Hà Nội"
    second.tracked_fields["property_place"] = "Ngõ 83 đường Đình Xuyên, TP Hà Nội"
    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[second],
    )

    assert result.event_counts == _counts()
    assert store.list_history_rows(event_type="CHANGED") == []


def test_history_store_only_marks_missing_inside_current_crawl_range(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    store.record_crawl(
        notice_kind="auction",
        start_date="01/06/2026",
        end_date="05/06/2026",
        snapshots=[
            _snapshot("1", publish_date="01/06/2026"),
            _snapshot("2", publish_date="04/06/2026"),
        ],
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="04/06/2026",
        end_date="06/06/2026",
        snapshots=[
            _snapshot("2", publish_date="04/06/2026"),
            _snapshot("3", publish_date="06/06/2026"),
        ],
    )

    assert result.event_counts == _counts(NEW=1)
    assert ("MISSING", "1") not in store.history_events()


def test_history_store_can_skip_missing_detection_for_partial_crawls(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot("1"), _snapshot("2")],
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot("1")],
        detect_missing=False,
    )

    assert result.event_counts == _counts()
    assert ("MISSING", "2") not in store.history_events()


def test_history_store_does_not_mark_missing_when_detail_still_exists(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot("1")],
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[],
        missing_exists_validator=lambda snapshot: True,
    )

    assert result.event_counts == _counts()
    assert ("MISSING", "1") not in store.history_events()


def test_history_store_marks_missing_when_detail_no_longer_exists(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot("1")],
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[],
        missing_exists_validator=lambda snapshot: False,
    )

    assert result.event_counts == _counts(MISSING=1)
    assert store.history_events()[-1] == ("MISSING", "1")


def test_history_store_skips_missing_when_validator_raises(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot("1")],
    )

    def fail(snapshot):
        raise RuntimeError("detail check failed")

    result = store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[],
        missing_exists_validator=fail,
    )

    assert result.event_counts == _counts()
    assert ("MISSING", "1") not in store.history_events()


def test_history_store_does_not_mark_missing_when_same_asset_appears_with_new_id(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        "574871",
        publish_date="30/05/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 1: Tài sản A,Lô 2: Tài sản B",
    )
    repost = _snapshot(
        "576556",
        publish_date="06/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 2: Tài sản B,Lô 1: Tài sản A",
    )
    store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="06/06/2026",
        snapshots=[first],
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="06/06/2026",
        snapshots=[repost],
        missing_exists_validator=lambda snapshot: False,
    )

    assert result.event_counts == _counts(NEW=1, SUSPECT_REPOST=1)
    assert ("MISSING", "574871") not in store.history_events()


def test_history_store_lists_history_rows_with_filters(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[_snapshot("1", name="Tài sản A")],
    )
    changed_snapshot = _snapshot("1", name="Tài sản B")
    changed_snapshot.tracked_fields["deadline"] = "12/06/2026"
    store.record_crawl(
        notice_kind="auction",
        start_date="05/06/2026",
        end_date="05/06/2026",
        snapshots=[changed_snapshot],
    )

    rows = store.list_history_rows(event_type="CHANGED")

    assert len(rows) == 1
    assert rows[0].event_type == "CHANGED"
    assert rows[0].notice_kind == "auction"
    assert rows[0].notice_id == "1"
    assert rows[0].detail_url == "https://dgts.moj.gov.vn/tin-1.html"
    assert rows[0].changed_fields == "deadline"
    assert "deadline:" in rows[0].changed_details
    assert "Cũ: " in rows[0].changed_details
    assert "Mới: 12/06/2026" in rows[0].changed_details
    assert rows[0].old_values == "deadline: "
    assert rows[0].new_values == "deadline: 12/06/2026"


def test_history_store_lists_history_rows_with_limit_and_offset(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    for notice_id in range(1, 6):
        store.record_crawl(
            notice_kind="auction",
            start_date="05/06/2026",
            end_date="05/06/2026",
            snapshots=[_snapshot(str(notice_id))],
            detect_missing=False,
        )

    first_page = store.list_history_rows(limit=2, offset=0)
    second_page = store.list_history_rows(limit=2, offset=2)

    assert [row.notice_id for row in first_page] == ["5", "4"]
    assert [row.notice_id for row in second_page] == ["3", "2"]


def test_history_store_marks_new_notice_with_same_asset_fingerprint_as_suspect_repost(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        "574871",
        publish_date="30/05/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 1: Tài sản A,Lô 2: Tài sản B",
    )
    repost = _snapshot(
        "576556",
        publish_date="06/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 2: Tài sản B,Lô 1: Tài sản A",
    )

    store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="30/05/2026",
        snapshots=[first],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="auction",
        start_date="06/06/2026",
        end_date="06/06/2026",
        snapshots=[repost],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SUSPECT_REPOST=1)
    assert store.history_events() == [
        ("NEW", "574871"),
        ("NEW", "576556"),
        ("SUSPECT_REPOST", "576556"),
    ]
    rows = store.list_history_rows(event_type="SUSPECT_REPOST")
    assert len(rows) == 1
    assert rows[0].matched_notice_id == "574871"
    assert "notice_id" in rows[0].changed_fields
    assert "Tin cũ: 574871" in rows[0].changed_details
    assert "Tin mới: 576556" in rows[0].changed_details
    assert "detail_url: https://dgts.moj.gov.vn/tin-574871.html" in rows[0].old_values
    assert "detail_url: https://dgts.moj.gov.vn/tin-576556.html" in rows[0].new_values


def test_history_store_does_not_match_generic_land_notices_with_different_places(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _auction_snapshot_with_place(
        "579346",
        publish_date="04/06/2026",
        place="thị trấn Mường tè cũ",
        deadline="17/06/2026",
    )
    different_place = _auction_snapshot_with_place(
        "581025",
        publish_date="07/06/2026",
        place="Xã Bum tở",
        deadline="01/07/2026",
    )

    store.record_crawl(
        notice_kind="auction",
        start_date="04/06/2026",
        end_date="04/06/2026",
        snapshots=[first],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="auction",
        start_date="07/06/2026",
        end_date="07/06/2026",
        snapshots=[different_place],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SAME_ASSET_NAME=1)
    assert store.list_history_rows(event_type="SUSPECT_REPOST") == []
    assert store.list_history_rows(event_type="CHANGED") == []


def test_history_store_marks_same_asset_name_when_fingerprint_differs(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _auction_snapshot_with_place(
        "579346",
        publish_date="04/06/2026",
        place="thị trấn Mường tè cũ",
        deadline="17/06/2026",
    )
    same_name_other_place = _auction_snapshot_with_place(
        "581025",
        publish_date="07/06/2026",
        place="Xã Bum tở",
        deadline="01/07/2026",
    )

    store.record_crawl(
        notice_kind="auction",
        start_date="04/06/2026",
        end_date="04/06/2026",
        snapshots=[first],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="auction",
        start_date="07/06/2026",
        end_date="07/06/2026",
        snapshots=[same_name_other_place],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SAME_ASSET_NAME=1)
    assert store.history_events()[-1] == ("SAME_ASSET_NAME", "581025")
    rows = store.list_history_rows(event_type="SAME_ASSET_NAME")
    assert len(rows) == 1
    assert rows[0].notice_id == "581025"
    assert rows[0].matched_notice_id == "579346"
    assert "asset_name" in rows[0].changed_fields
    assert "property_place" in rows[0].changed_fields
    assert "Tin cũ: 579346" in rows[0].changed_details
    assert "Tin mới: 581025" in rows[0].changed_details
    assert "property_place: thị trấn Mường tè cũ" in rows[0].old_values
    assert "property_place: Xã Bum tở" in rows[0].new_values


def test_history_store_treats_legacy_missing_place_match_as_suspect_repost(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    legacy = _auction_snapshot_without_place(
        "579378",
        publish_date="04/06/2026",
        deadline="29/06/2026",
    )
    repost = _auction_snapshot_with_place(
        "581036",
        publish_date="07/06/2026",
        place="Thửa đất số 116, tờ bản đồ số 28, địa chỉ: Thôn Bãi Giếng 2, xã Cam Hải Tây, huyện Cam Lâm, tỉnh Khánh Hoà (nay là xã Cam Lâm, tỉnh Khánh Hòa)",
        deadline="29/06/2026",
    )
    legacy.tracked_fields["asset_name"] = "Thông báo việc đấu giá đối với danh mục tài sản: \tQuyền sử dụng đất tại thửa 116, tờ bản đồ số 28, địa chỉ: Thôn Bãi Giếng 2, xã Cam Hải Tây, huyện Cam Lâm, tỉnh Khánh Hoà (nay là xã Cam Lâm, tỉnh Khánh Hòa), theo Giấy chứng nhận quyền sử dụng đất, quyền sở hữu nhà ở và tài sản khác gắn liền với đất số phát hành: CU 232297, số vào sổ cấp giấy chứng nhận số: CS2340 do Sở Tài nguyên và Môi trường tỉnh Khánh Hoà cấp ngày 04/02/2020"
    repost.tracked_fields["asset_name"] = legacy.tracked_fields["asset_name"]
    legacy.tracked_fields["province"] = "Khánh Hòa"
    repost.tracked_fields["province"] = "Khánh Hòa"
    legacy.tracked_fields["owner_name"] = "Agribank Chi nhánh Tân Bình"
    repost.tracked_fields["owner_name"] = "Agribank Chi nhánh Tân Bình"
    legacy.tracked_fields["start_price"] = 41079786987
    repost.tracked_fields["start_price"] = 41079786987
    legacy.tracked_fields["deposit"] = 4107978698
    repost.tracked_fields["deposit"] = 4107978698

    store.record_crawl(
        notice_kind="auction",
        start_date="04/06/2026",
        end_date="04/06/2026",
        snapshots=[legacy],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="auction",
        start_date="07/06/2026",
        end_date="07/06/2026",
        snapshots=[repost],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SUSPECT_REPOST=1)
    assert store.list_history_rows(event_type="SAME_ASSET_NAME") == []


def test_history_store_marks_cross_id_reappeared_when_missing_asset_returns_with_new_id(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        "574871",
        publish_date="30/05/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 1: Tài sản A,Lô 2: Tài sản B",
    )
    repost = _snapshot(
        "576556",
        publish_date="06/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 2: Tài sản B,Lô 1: Tài sản A",
    )
    store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="30/05/2026",
        snapshots=[first],
    )
    store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="30/05/2026",
        snapshots=[],
        missing_exists_validator=lambda snapshot: False,
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="06/06/2026",
        end_date="06/06/2026",
        snapshots=[repost],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, REAPPEARED=1, SUSPECT_REPOST=1)
    assert store.history_events()[-3:] == [
        ("NEW", "576556"),
        ("SUSPECT_REPOST", "576556"),
        ("REAPPEARED", "576556"),
    ]
    rows = store.list_history_rows(event_type="REAPPEARED")
    assert rows[0].notice_id == "576556"
    assert "notice_id" in rows[0].changed_fields
    assert "Tin cũ: 574871" in rows[0].changed_details
    assert "Tin mới: 576556" in rows[0].changed_details


def test_history_store_marks_cross_id_changed_for_same_asset_with_business_change(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        "574871",
        publish_date="30/05/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Xe Chevrolet Colorado",
    )
    first.tracked_fields["deadline"] = "10/06/2026"
    changed_repost = HistorySnapshot(
        notice_kind="auction",
        notice_id="576556",
        publish_date="06/06/2026",
        detail_url="https://dgts.moj.gov.vn/tin-576556.html",
        tracked_fields={
            "publish_date": "06/06/2026",
            "asset_name": "Thông báo việc đấu giá đối với danh mục tài sản: Xe Chevrolet Colorado",
            "owner_name": "Cơ quan A",
            "start_price": 1000,
            "deadline": "12/06/2026",
        },
        raw_payload={"id": "576556", "propertyName": "Xe Chevrolet Colorado"},
    )
    store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="30/05/2026",
        snapshots=[first],
        detect_missing=False,
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="06/06/2026",
        end_date="06/06/2026",
        snapshots=[changed_repost],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, CHANGED=1, SUSPECT_REPOST=1)
    rows = store.list_history_rows(event_type="CHANGED")
    assert len(rows) == 1
    assert rows[0].notice_id == "576556"
    assert rows[0].changed_fields == "deadline"
    assert rows[0].old_values == "deadline: 10/06/2026"
    assert rows[0].new_values == "deadline: 12/06/2026"


def test_history_store_does_not_mark_cross_id_changed_for_identifier_only_change(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        "574871",
        publish_date="30/05/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Xe Chevrolet Colorado",
    )
    repost = _snapshot(
        "576556",
        publish_date="06/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Xe Chevrolet Colorado",
    )
    store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="30/05/2026",
        snapshots=[first],
        detect_missing=False,
    )

    result = store.record_crawl(
        notice_kind="auction",
        start_date="06/06/2026",
        end_date="06/06/2026",
        snapshots=[repost],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SUSPECT_REPOST=1)
    assert store.list_history_rows(event_type="CHANGED") == []


def test_history_store_ignores_notice_code_for_cross_id_changed(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _auction_snapshot_with_place(
        "579346",
        publish_date="04/06/2026",
        place="Xã Bum tở",
        deadline="17/06/2026",
    )
    repost = _auction_snapshot_with_place(
        "581025",
        publish_date="07/06/2026",
        place="Xã Bum tở",
        deadline="17/06/2026",
    )

    store.record_crawl(
        notice_kind="auction",
        start_date="04/06/2026",
        end_date="04/06/2026",
        snapshots=[first],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="auction",
        start_date="07/06/2026",
        end_date="07/06/2026",
        snapshots=[repost],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SUSPECT_REPOST=1)
    assert store.list_history_rows(event_type="CHANGED") == []


def test_history_store_does_not_mark_same_day_duplicate_as_suspect_repost(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    first = _snapshot(
        "28036",
        publish_date="01/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Xe Chevrolet Colorado",
    )
    duplicate_same_day = _snapshot(
        "28037",
        publish_date="01/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Xe Chevrolet Colorado",
    )

    store.record_crawl(
        notice_kind="select-org-result",
        start_date="01/06/2026",
        end_date="01/06/2026",
        snapshots=[first],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="select-org-result",
        start_date="01/06/2026",
        end_date="01/06/2026",
        snapshots=[duplicate_same_day],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1)
    assert store.list_history_rows(event_type="SUSPECT_REPOST") == []


def test_history_store_orders_suspect_repost_old_and_new_by_publish_date(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    newer_seen_first = _snapshot(
        "576556",
        publish_date="06/06/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 2: Tài sản B,Lô 1: Tài sản A",
    )
    older_seen_later = _snapshot(
        "574871",
        publish_date="30/05/2026",
        name="Thông báo việc đấu giá đối với danh mục tài sản: Lô 1: Tài sản A,Lô 2: Tài sản B",
    )

    store.record_crawl(
        notice_kind="auction",
        start_date="06/06/2026",
        end_date="06/06/2026",
        snapshots=[newer_seen_first],
        detect_missing=False,
    )
    result = store.record_crawl(
        notice_kind="auction",
        start_date="30/05/2026",
        end_date="30/05/2026",
        snapshots=[older_seen_later],
        detect_missing=False,
    )

    assert result.event_counts == _counts(NEW=1, SUSPECT_REPOST=1)
    rows = store.list_history_rows(event_type="SUSPECT_REPOST")
    assert rows[0].matched_notice_id == "576556"
    assert "Tin cũ: 574871" in rows[0].changed_details
    assert "Tin mới: 576556" in rows[0].changed_details
    assert "detail_url: https://dgts.moj.gov.vn/tin-574871.html" in rows[0].old_values
    assert "detail_url: https://dgts.moj.gov.vn/tin-576556.html" in rows[0].new_values


def test_history_store_displays_legacy_suspect_repost_fields_by_publish_date(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)
    with store._connect() as conn:
        run_id = store._insert_run(conn, "2026-06-06T21:00:00", "auction", "30/05/2026", "06/06/2026", 1)
        snapshot = _snapshot("574871", publish_date="30/05/2026")
        store._insert_event(
            conn,
            run_id,
            "2026-06-06T21:00:00",
            snapshot,
            "SUSPECT_REPOST",
            {
                "matched_notice_id": {"old": "576556", "new": "574871"},
                "matched_publish_date": {"old": "06/06/2026", "new": "30/05/2026"},
                "matched_detail_url": {
                    "old": "https://dgts.moj.gov.vn/tin-576556.html",
                    "new": "https://dgts.moj.gov.vn/tin-574871.html",
                },
                "match_type": {"old": "", "new": "exact_asset_fingerprint"},
            },
            "",
            "{}",
            "hash",
        )

    row = store.list_history_rows(event_type="SUSPECT_REPOST")[0]

    assert row.matched_notice_id == "576556"
    assert "Tin cũ: 574871" in row.changed_details
    assert "Tin mới: 576556" in row.changed_details
    assert "detail_url: https://dgts.moj.gov.vn/tin-574871.html" in row.old_values
    assert "detail_url: https://dgts.moj.gov.vn/tin-576556.html" in row.new_values


def test_history_store_creates_indexes_for_history_filters(tmp_path):
    db_path = tmp_path / "history.sqlite"
    store = HistoryStore(db_path)

    with store._connect() as conn:
        indexes = {
            row["name"]
            for row in conn.execute("PRAGMA index_list('notice_history')").fetchall()
        }

    assert "idx_notice_history_kind_event_id" in indexes
    assert "idx_notice_history_event_id" in indexes
