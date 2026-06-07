from dgts_crawler.classifier import SHEET_ORDER, classify_notice, score_notice


def test_bank_owner_signal_classifies_to_ngan_hang():
    notice = {
        "propertyName": "Quyền sử dụng đất và tài sản gắn liền với đất",
        "fullname": "Ngân hàng TMCP Quốc Tế Việt Nam",
        "propertyTypeName": "Tài sản bảo đảm theo quy định của pháp luật",
    }

    assert classify_notice(notice) == "Ngân hàng"


def test_land_notice_classifies_to_dat_dai():
    notice = {
        "propertyName": "Quyền sử dụng đất tại thửa đất số 320",
        "fullname": "Ủy ban nhân dân xã",
        "propertyTypeName": "Tài sản là quyền sử dụng đất theo quy định của pháp luật về đất đai",
    }

    assert classify_notice(notice) == "Đất đai"


def test_liquidation_vehicle_notice_classifies_to_thanh_ly():
    notice = {
        "propertyName": "Thanh lý xe ô tô 05 chỗ đã qua sử dụng",
        "fullname": "Công ty cổ phần thương mại",
        "propertyTypeName": "Tài sản cố định của doanh nghiệp",
    }

    assert classify_notice(notice) == "Thanh lý"


def test_liquidation_demolition_notice_classifies_to_thanh_ly():
    notice = {
        "propertyName": "Phá dỡ công trình, bán vật liệu thu hồi và vật liệu thải",
        "fullname": "Ban quản lý dự án",
    }

    assert classify_notice(notice) == "Thanh lý"


def test_thi_hanh_an_owner_signal_beats_land_text():
    notice = {
        "propertyName": "Quyền sử dụng đất tại thành phố Cần Thơ",
        "fullname": "Thi hành án dân sự thành phố Cần Thơ",
        "propertyTypeName": "Tài sản thi hành án theo quy định của pháp luật",
    }

    assert classify_notice(notice) == "Thi hành án"


def test_cong_an_signal_classifies_to_cong_an():
    notice = {
        "propertyName": "Tang vật vi phạm hành chính bị tịch thu sung quỹ",
        "fullname": "Công an tỉnh An Giang",
    }

    assert classify_notice(notice) == "Công an"


def test_khoang_san_signal_classifies_to_khoang_san():
    notice = {
        "propertyName": "Quyền khai thác khoáng sản mỏ đá xây dựng",
        "propertyTypeName": "Tài sản là quyền khai thác khoáng sản",
    }

    assert classify_notice(notice) == "Khoáng sản"


def test_dien_luc_signal_classifies_to_dien_luc():
    notice = {
        "propertyName": "Thanh lý vật tư điện, cáp điện và máy biến áp",
        "fullname": "Công ty Điện lực Hà Nội",
    }

    assert classify_notice(notice) == "Điện lực"


def test_company_signal_classifies_to_cong_ty():
    notice = {
        "propertyName": "Tài sản thuộc sở hữu của cá nhân, tổ chức",
        "fullname": "Công ty TNHH Một Thành Viên Dịch Vụ",
        "propertyTypeName": "Tài sản thuộc sở hữu của cá nhân, tổ chức",
    }

    assert classify_notice(notice) == "Công ty"


def test_score_notice_exposes_weighted_reasoning():
    notice = {
        "propertyName": "Quyền sử dụng đất",
        "fullname": "Ngân hàng TMCP Quốc Tế Việt Nam",
    }

    scores = score_notice(notice)

    assert scores["Ngân hàng"] > scores["Đất đai"]


def test_unknown_notice_falls_back_to_thanh_ly():
    notice = {"propertyName": "Tài sản khác bán đấu giá"}

    assert classify_notice(notice) == "Thanh lý"


def test_sheet_order_contains_requested_groups():
    assert SHEET_ORDER == [
        "Thanh lý",
        "Đất đai",
        "Ngân hàng",
        "Thi hành án",
        "Công an",
        "Khoáng sản",
        "Điện lực",
        "Công ty",
    ]
