from dgts_crawler.transform import (
    merge_detail,
    normalize_notice,
    normalize_select_org_notice,
    normalize_select_org_notices,
    normalize_select_org_result_notices,
)


def test_merge_detail_sums_multiple_property_rows():
    detail_page = {
        "items": [
            {
                "propertyStartPrice": 1000,
                "deposit": 100,
                "propertyTypeId": "178",
                "propertyTypeName": "Tài sản thi hành án theo quy định của pháp luật",
            },
            {"propertyStartPrice": 2500, "deposit": 250},
        ]
    }

    merged = merge_detail(detail_page)
    assert merged["items"] == detail_page["items"]
    assert {key: value for key, value in merged.items() if key != "items"} == {
        "propertyStartPrice": 3500,
        "deposit": 350,
        "depositUnit": 0,
        "propertyPlace": "",
        "propertyTypeId": "178",
        "propertyTypeName": "Tài sản thi hành án theo quy định của pháp luật",
    }


def test_merge_detail_preserves_percent_deposit_unit():
    detail_page = {
        "items": [
            {
                "propertyStartPrice": 1000,
                "deposit": 20,
                "depositUnit": 1,
                "strDeposit": "20%",
            }
        ]
    }

    assert merge_detail(detail_page)["deposit"] == 20
    assert merge_detail(detail_page)["depositUnit"] == 1


def test_normalize_notice_maps_api_record_to_excel_row():
    notice = {
        "id": 579853,
        "propertyName": "Quyền sử dụng đất tại Tây Ninh",
        "publishTime1": 1780638042132,
        "publishTime2": None,
        "aucRegTimeEnd": 1782122400000,
        "fullname": "Ngân hàng TMCP Quốc Tế Việt Nam",
        "org_name": "Công ty Đấu giá Hợp danh Bất động sản Việt",
        "propertyTypeId": 174,
        "propertyTypeName": "Tài sản bảo đảm",
    }
    detail = {
        "propertyStartPrice": 579000000,
        "deposit": 57900000,
        "propertyPlace": "Xã Phước Ninh, Huyện Dương Minh Châu, Tỉnh Tây Ninh",
    }

    row = normalize_notice(notice, detail)[0]

    assert row.sheet_name == "Ngân hàng"
    assert row.values[1] == "05/06/2026"
    assert row.values[2] == "Lần 1"
    assert row.values[3] == "Ngân hàng TMCP Quốc Tế Việt Nam"
    assert row.values[6] == "Quyền sử dụng đất tại Tây Ninh"
    assert row.values[8] == "Xã Phước Ninh, Huyện Dương Minh Châu, Tỉnh Tây Ninh"
    assert row.values[9] == 579000000
    assert row.values[10] == 57900000
    assert row.values[13] == "22/06/2026"


def test_normalize_notice_keeps_percent_deposit_marker():
    notice = {
        "id": 1,
        "propertyName": "Thanh lý vật tư",
        "publishTime1": 1780638042132,
        "aucRegTimeEnd": 1782122400000,
        "fullname": "Cơ quan A",
    }
    detail = {
        "propertyStartPrice": 100000000,
        "deposit": 20,
        "depositUnit": 1,
    }

    row = normalize_notice(notice, detail)[0]

    assert row.values[10] == "20%"


def test_normalize_notice_uses_detail_property_type_for_classification():
    notice = {
        "id": 579800,
        "propertyName": "Quyền sử dụng đất và tài sản gắn liền với đất",
        "publishTime1": 1780634969667,
        "aucRegTimeEnd": 1782813600000,
        "fullname": "Cá nhân có tài sản",
        "propertyTypeId": None,
        "propertyTypeName": None,
    }
    detail = {
        "propertyStartPrice": 100000000,
        "deposit": 10000000,
        "propertyPlace": "Hà Nội",
        "propertyTypeId": "178",
        "propertyTypeName": "Tài sản thi hành án theo quy định của pháp luật về thi hành án dân sự",
    }

    row = normalize_notice(notice, detail)[0]

    assert row.sheet_name == "Thi hành án"


def test_normalize_notice_returns_one_row_per_property_info_item():
    notice = {
        "id": 581036,
        "propertyName": "Thông báo việc đấu giá đối với danh mục tài sản: Xe ô tô và thiết bị",
        "publishTime1": 1780638042132,
        "publishTime2": 1780724442132,
        "fullname": "Cơ quan A",
        "orgName": "Tổ chức đấu giá A",
        "aucRegTimeStart": 1780851600000,
        "aucRegTimeEnd": 1781024400000,
        "aucRegPlace": "Trụ sở tổ chức đấu giá",
        "depositTimeStart": 1781110800000,
        "depositTimeEnd": 1781197200000,
    }
    detail = {
        "items": [
            {
                "propertyName": "Xe ô tô",
                "propertyAmount": "01",
                "propertyPlace": "Hà Nội",
                "propertyStartPrice": 100000000,
                "deposit": 10000000,
                "depositUnit": 0,
                "propertyQuality": "Đã qua sử dụng",
            },
            {
                "propertyName": "Thiết bị kèm theo",
                "propertyAmount": "02",
                "propertyPlace": "Hà Nội",
                "propertyStartPrice": 20000000,
                "deposit": 20,
                "depositUnit": 1,
                "detail": "Theo hiện trạng",
            },
        ]
    }

    rows = normalize_notice(notice, detail)

    assert len(rows) == 2
    assert rows[0].values == [
        0,
        "06/06/2026",
        "Lần 2",
        "Cơ quan A",
        "Tổ chức đấu giá A",
        "",
        "Xe ô tô",
        "01",
        "Hà Nội",
        100000000,
        10000000,
        "Đã qua sử dụng",
        "08/06/2026",
        "10/06/2026",
        "Trụ sở tổ chức đấu giá",
        "11/06/2026",
        "12/06/2026",
        "https://dgts.moj.gov.vn/thong-bao-cong-khai-viec-dau-gia/thong-bao-viec-dau-gia-doi-voi-danh-muc-tai-san-xe-o-to-va-thiet-bi-581036.html",
    ]
    assert rows[1].values[6] == "Thiết bị kèm theo"
    assert rows[1].values[7] == "02"
    assert rows[1].values[10] == "20%"
    assert rows[1].values[11] == "Theo hiện trạng"


def test_normalize_select_org_notice_maps_detail_and_property_rows_to_excel_row():
    notice = {
        "id": 105862,
        "propertyName": "Quyền sử dụng đất tại Hòa Bình",
        "lastUpdated": 1780664230000,
        "receiveTimeStart": 1780851600000,
        "receiveTimeEnd": 1781024400000,
    }
    detail = {
        "items": [
            {
                "id": 105862,
                "ownerFullname": "Công ty TNHH MTV QLN và KTTS Ngân hàng TMCP Quốc tế Việt Nam",
                "ownerAddress": "4 Tôn Thất Tùng",
                "addrOwner": "4 Tôn Thất Tùng, Thành phố Hà Nội",
                "genDate": 1780657028537,
                "fromDate": 1780851600000,
                "toDate": 1781024400000,
                "addressReceive": "Tầng 12, Tòa nhà Coninco Tower",
                "contactInfo": "Ông Nguyễn Văn Huyện - SĐT 0914832245",
                "propertyName": "Quyền sử dụng đất tại Hòa Bình",
            }
        ]
    }
    property_rows = [
        {
            "propertyTypeName": "Tài sản bảo đảm",
            "propertyName": "Quyền sử dụng đất thửa 122",
            "propertyAmount": "01",
            "propertyQuality": "Theo giấy chứng nhận số DI770021",
            "propertyStartPrice": 4050200000,
        },
        {
            "propertyName": "Nhà ở gắn liền với đất",
            "propertyAmount": "01",
            "propertyQuality": "Hiện trạng sử dụng bình thường",
            "propertyStartPrice": 120000000,
        },
    ]

    row = normalize_select_org_notice(notice, detail, property_rows)

    assert row.values == [
        0,
        "05/06/2026",
        "Tài sản bảo đảm - Quyền sử dụng đất thửa 122\nNhà ở gắn liền với đất",
        "Công ty TNHH MTV QLN và KTTS Ngân hàng TMCP Quốc tế Việt Nam",
        "4 Tôn Thất Tùng, Thành phố Hà Nội",
        "01\n01",
        "Theo giấy chứng nhận số DI770021\nHiện trạng sử dụng bình thường",
        4170200000,
        "08/06/2026",
        "10/06/2026",
        "Tầng 12, Tòa nhà Coninco Tower",
        "Ông Nguyễn Văn Huyện - SĐT 0914832245",
        (
            "https://dgts.moj.gov.vn/thong-bao-lua-chon-to-chuc-dau-gia/"
            "quyen-su-dung-dat-tai-hoa-binh-105862.html"
        ),
    ]


def test_normalize_select_org_notices_returns_one_excel_row_per_property_row():
    notice = {
        "id": 105900,
        "propertyName": "Xe ô tô thanh lý",
        "lastUpdated": 1780664230000,
    }
    detail = {
        "items": [
            {
                "id": 105900,
                "ownerFullname": "Viện Khoa học và Thủy Lợi Việt Nam",
                "addrOwner": "171 Tây Sơn, phường Kim Liên, thành phố Hà Nội",
                "genDate": 1780657028537,
                "fromDate": 1780851600000,
                "toDate": 1781024400000,
                "addressReceive": "171 Tây Sơn, phường Kim Liên, thành phố Hà Nội",
                "contactInfo": "02438522086",
                "propertyName": "Xe ô tô thanh lý",
            }
        ]
    }
    property_rows = [
        {
            "propertyTypeName": "Tài sản nhà nước",
            "propertyName": "Xe ô tô Toyota Corolla, BKS: 31A-3402, sản xuất năm 1998",
            "propertyAmount": "01",
            "propertyQuality": "Xe cũ, đã qua sử dụng",
            "propertyStartPrice": 35000000,
        },
        {
            "propertyTypeName": "Tài sản nhà nước",
            "propertyName": "Xe ô tô Toyota Camry, BKS 31A-5958, sản xuất năm 2003",
            "propertyAmount": "01",
            "propertyQuality": "Xe cũ, đã qua sử dụng",
            "propertyStartPrice": 85000000,
        },
        {
            "propertyTypeName": "Tài sản nhà nước",
            "propertyName": "Xe ô tô Toyota Hiace, BKS: 31A-7650, năm sản xuất 2006",
            "propertyAmount": "01",
            "propertyQuality": "Xe cũ, đã qua sử dụng",
            "propertyStartPrice": 100000000,
        },
    ]

    rows = normalize_select_org_notices(notice, detail, property_rows)

    assert len(rows) == 3
    assert [row.values[2] for row in rows] == [
        "Tài sản nhà nước - Xe ô tô Toyota Corolla, BKS: 31A-3402, sản xuất năm 1998",
        "Tài sản nhà nước - Xe ô tô Toyota Camry, BKS 31A-5958, sản xuất năm 2003",
        "Tài sản nhà nước - Xe ô tô Toyota Hiace, BKS: 31A-7650, năm sản xuất 2006",
    ]
    assert [row.values[7] for row in rows] == [35000000, 85000000, 100000000]
    assert all(row.values[3] == "Viện Khoa học và Thủy Lợi Việt Nam" for row in rows)
    assert all(row.values[11] == "02438522086" for row in rows)


def test_normalize_select_org_notice_falls_back_to_list_item_when_detail_is_missing():
    notice = {
        "id": 105861,
        "propertyName": "Xe ô tô MITSUBISHI",
        "lastUpdated": 1780663190000,
        "receiveTimeStart": 1780851600000,
        "receiveTimeEnd": 1781024400000,
    }

    row = normalize_select_org_notice(notice, {}, [])

    assert row.values[1] == "05/06/2026"
    assert row.values[2] == "Xe ô tô MITSUBISHI"
    assert row.values[8] == "08/06/2026"
    assert row.values[9] == "10/06/2026"
    assert row.values[12].endswith("/xe-o-to-mitsubishi-105861.html")


def test_normalize_select_org_notice_uses_public_date_from_list_before_detail_gen_date():
    notice = {
        "id": 105867,
        "propertyName": "Tài sản công khai hôm nay",
        "lastUpdated": 1780729740000,
    }
    detail = {
        "items": [
            {
                "propertyName": "Tài sản công khai hôm nay",
                "ownerFullname": "Cơ quan A",
                "genDate": 1780540000000,
            }
        ]
    }

    row = normalize_select_org_notice(notice, detail, [])

    assert row.values[1] == "06/06/2026"


def test_normalize_select_org_result_notices_returns_one_row_per_property_row():
    notice = {
        "id": 25941,
        "propertyName": "Xe ô tô cứu thương BKS 14C 1248",
        "orgName": "Trung tâm dịch vụ đấu giá tài sản Quảng Ninh",
        "publishTime": 1780663701000,
        "ownerFullname": "Bệnh viện Đa khoa khu vực Cẩm Phả",
    }
    owner = {
        "fullname": "Bệnh viện Đa khoa khu vực Cẩm Phả",
        "addrOwner": "Tổ 1, Khu 3, phường Cẩm Thịnh, Tỉnh Quảng Ninh",
    }
    history = {
        "orgInfo": {
            "orgName": "Trung tâm dịch vụ đấu giá tài sản Quảng Ninh",
            "addrFull": "Tầng 5, trụ sở Trung tâm Phục vụ hành chính công tỉnh Quảng Ninh",
            "foneNumber": "0203.3508.336",
        },
        "property": [
            {
                "propertyName": "Xe ô tô cứu thương BKS 14C 1248",
                "propertyAmount": "01",
                "propertyQuality": "Đã qua sử dụng",
                "propertyStartPrice": 19880000,
                "propertyTypeName": "",
            },
            {
                "propertyName": "Thiết bị y tế kèm theo",
                "propertyAmount": "02",
                "propertyQuality": "Đã qua sử dụng",
                "propertyStartPrice": 5000000,
            },
        ],
    }
    info = {
        "publishTime": 1780663701000,
        "subPropertyName": "Xe ô tô cứu thương BKS 14C 1248",
    }

    rows = normalize_select_org_result_notices(notice, owner, history, info)

    assert len(rows) == 2
    assert rows[0].values == [
        0,
        "05/06/2026",
        "Xe ô tô cứu thương BKS 14C 1248",
        "Bệnh viện Đa khoa khu vực Cẩm Phả",
        "Tổ 1, Khu 3, phường Cẩm Thịnh, Tỉnh Quảng Ninh",
        "01",
        "Đã qua sử dụng",
        19880000,
        "Trung tâm dịch vụ đấu giá tài sản Quảng Ninh",
        "Tầng 5, trụ sở Trung tâm Phục vụ hành chính công tỉnh Quảng Ninh",
        "0203.3508.336",
        (
            "https://dgts.moj.gov.vn/thong-bao-ket-qua-lua-chon-to-chuc-dau-gia/"
            "xe-o-to-cuu-thuong-bks-14c-1248-25941.html"
        ),
    ]
    assert rows[1].values[2] == "Thiết bị y tế kèm theo"
    assert rows[1].values[7] == 5000000


def test_normalize_select_org_result_notices_falls_back_to_list_item_when_detail_missing():
    notice = {
        "id": 25942,
        "propertyName": "Tài sản fallback",
        "orgName": "Tổ chức fallback",
        "ownerFullname": "Cơ quan fallback",
        "publishTime": 1780663701000,
    }

    rows = normalize_select_org_result_notices(notice, {}, {}, {})

    assert len(rows) == 1
    assert rows[0].values[1] == "05/06/2026"
    assert rows[0].values[2] == "Tài sản fallback"
    assert rows[0].values[3] == "Cơ quan fallback"
    assert rows[0].values[8] == "Tổ chức fallback"
    assert rows[0].values[11].endswith("/tai-san-fallback-25942.html")


def test_normalize_select_org_result_notices_uses_public_date_from_list_before_detail_info():
    notice = {
        "id": 62320,
        "propertyName": "Kết quả công khai hôm nay",
        "orgName": "Tổ chức A",
        "ownerFullname": "Cơ quan A",
        "publishTime": 1780735786000,
    }
    info = {
        "publishTime": 1780540000000,
        "subPropertyName": "Kết quả công khai hôm nay",
    }

    rows = normalize_select_org_result_notices(notice, {}, {}, info)

    assert rows[0].values[1] == "06/06/2026"
