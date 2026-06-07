from dgts_crawler.utils import (
    build_detail_url,
    build_select_org_detail_url,
    build_select_org_result_detail_url,
    format_millis_date,
    make_notice_code,
)


def test_format_millis_date_uses_vietnam_date():
    assert format_millis_date(1780638042132) == "05/06/2026"


def test_format_millis_date_returns_empty_for_missing_value():
    assert format_millis_date(None) == ""


def test_make_notice_code_is_stable_from_id():
    assert make_notice_code(579853) == "TS_579853"


def test_build_detail_url_slugifies_vietnamese_title():
    url = build_detail_url(
        579853,
        "Quyền sử dụng đất tại Xã Phước Ninh, Tỉnh Tây Ninh",
    )

    assert url == (
        "https://dgts.moj.gov.vn/thong-bao-cong-khai-viec-dau-gia/"
        "quyen-su-dung-dat-tai-xa-phuoc-ninh-tinh-tay-ninh-579853.html"
    )


def test_build_select_org_detail_url_uses_select_org_prefix():
    url = build_select_org_detail_url(
        105862,
        "Quyền sử dụng đất tại Hòa Bình",
    )

    assert url == (
        "https://dgts.moj.gov.vn/thong-bao-lua-chon-to-chuc-dau-gia/"
        "quyen-su-dung-dat-tai-hoa-binh-105862.html"
    )


def test_build_select_org_result_detail_url_uses_result_prefix():
    url = build_select_org_result_detail_url(
        25941,
        "Xe ô tô cứu thương BKS 14C 1248",
    )

    assert url == (
        "https://dgts.moj.gov.vn/thong-bao-ket-qua-lua-chon-to-chuc-dau-gia/"
        "xe-o-to-cuu-thuong-bks-14c-1248-25941.html"
    )
