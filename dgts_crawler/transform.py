from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .classifier import classify_notice
from .utils import (
    build_detail_url,
    build_select_org_detail_url,
    build_select_org_result_detail_url,
    format_millis_date,
)


@dataclass(frozen=True)
class AuctionRow:
    sheet_name: str
    values: list[Any]


PROVINCES = [
    "An Giang", "Bà Rịa - Vũng Tàu", "Bạc Liêu", "Bắc Giang", "Bắc Kạn",
    "Bắc Ninh", "Bến Tre", "Bình Dương", "Bình Định", "Bình Phước",
    "Bình Thuận", "Cà Mau", "Cao Bằng", "Cần Thơ", "Đà Nẵng", "Đắk Lắk",
    "Đắk Nông", "Điện Biên", "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Giang",
    "Hà Nam", "Hà Nội", "Hà Tĩnh", "Hải Dương", "Hải Phòng", "Hậu Giang",
    "Hòa Bình", "Hưng Yên", "Khánh Hòa", "Kiên Giang", "Kon Tum", "Lai Châu",
    "Lâm Đồng", "Lạng Sơn", "Lào Cai", "Long An", "Nam Định", "Nghệ An",
    "Ninh Bình", "Ninh Thuận", "Phú Thọ", "Phú Yên", "Quảng Bình",
    "Quảng Nam", "Quảng Ngãi", "Quảng Ninh", "Quảng Trị", "Sóc Trăng",
    "Sơn La", "Tây Ninh", "Thái Bình", "Thái Nguyên", "Thanh Hóa",
    "Thừa Thiên Huế", "Tiền Giang", "TP Hồ Chí Minh", "Hồ Chí Minh",
    "Trà Vinh", "Tuyên Quang", "Vĩnh Long", "Vĩnh Phúc", "Yên Bái",
]


def merge_detail(detail_page: Mapping[str, Any]) -> dict[str, Any]:
    items = detail_page.get("items") or []
    start_price = sum(_number(item.get("propertyStartPrice")) for item in items)
    deposit_unit = next((item.get("depositUnit") for item in items if item.get("depositUnit") is not None), 0)
    deposit = _merge_deposit(items, deposit_unit)
    property_place = next((str(item.get("propertyPlace") or "") for item in items if item.get("propertyPlace")), "")
    property_type_id = next((item.get("propertyTypeId") for item in items if item.get("propertyTypeId")), None)
    property_type_name = next((item.get("propertyTypeName") for item in items if item.get("propertyTypeName")), None)
    return {
        "items": items,
        "propertyStartPrice": start_price,
        "deposit": deposit,
        "depositUnit": deposit_unit,
        "propertyPlace": property_place,
        "propertyTypeId": property_type_id,
        "propertyTypeName": property_type_name,
    }


def normalize_notice(notice: Mapping[str, Any], detail: Mapping[str, Any]) -> list[AuctionRow]:
    classification_notice = dict(notice)
    for key in ("propertyTypeId", "propertyTypeName", "propertyPlace"):
        if detail.get(key) and not classification_notice.get(key):
            classification_notice[key] = detail.get(key)
    sheet_name = classify_notice(classification_notice)
    publish_date = format_millis_date(notice.get("publishTime2") or notice.get("publishTime1"))
    publish_round = "Lần 2" if notice.get("publishTime2") else "Lần 1"
    property_name = str(notice.get("propertyName") or "")
    notice_id = notice.get("id")
    property_rows = _auction_property_rows(notice, detail)
    common_values = [
        0,
        publish_date,
        publish_round,
        notice.get("fullname") or "Xem chi tiết trong link",
        notice.get("orgName") or notice.get("org_name") or "",
        _auction_info(notice),
        "",
        "",
        "",
        0,
        0,
        "",
        format_millis_date(notice.get("aucRegTimeStart")),
        format_millis_date(notice.get("aucRegTimeEnd")),
        _auction_registration_info(notice),
        format_millis_date(_first_present(notice, "depositTimeStart", "depositStartTime", "depositStartDate")),
        format_millis_date(_first_present(notice, "depositTimeEnd", "depositEndTime", "depositEndDate")),
        build_detail_url(notice_id, property_name),
    ]

    rows: list[AuctionRow] = []
    for property_row in property_rows:
        values = list(common_values)
        values[6] = _auction_property_name(property_row, property_name, notice)
        values[7] = str(property_row.get("propertyAmount") or "").strip()
        values[8] = str(property_row.get("propertyPlace") or detail.get("propertyPlace") or "").strip()
        values[9] = _number(property_row.get("propertyStartPrice"))
        values[10] = _format_deposit(property_row.get("deposit"), property_row.get("depositUnit"))
        values[11] = str(property_row.get("detail") or property_row.get("propertyQuality") or "").strip()
        rows.append(AuctionRow(sheet_name=sheet_name, values=values))
    return rows


def normalize_select_org_notice(
    notice: Mapping[str, Any],
    detail: Mapping[str, Any],
    property_rows: list[Mapping[str, Any]],
) -> AuctionRow:
    detail_item = _first_item(detail)
    property_name = str(detail_item.get("propertyName") or notice.get("propertyName") or "")
    values = [
        0,
        format_millis_date(notice.get("lastUpdated") or detail_item.get("lastUpdated") or detail_item.get("genDate")),
        _join_property_names(property_rows) or property_name,
        detail_item.get("ownerFullname") or "",
        detail_item.get("addrOwner") or detail_item.get("ownerAddress") or "",
        _join_property_field(property_rows, "propertyAmount"),
        _join_property_field(property_rows, "propertyQuality"),
        sum(_number(row.get("propertyStartPrice")) for row in property_rows),
        format_millis_date(detail_item.get("fromDate") or notice.get("receiveTimeStart")),
        format_millis_date(detail_item.get("toDate") or notice.get("receiveTimeEnd")),
        detail_item.get("addressReceive") or "",
        detail_item.get("contactInfo") or "",
        build_select_org_detail_url(notice.get("id"), property_name),
    ]
    return AuctionRow(sheet_name="", values=values)


def normalize_select_org_notices(
    notice: Mapping[str, Any],
    detail: Mapping[str, Any],
    property_rows: list[Mapping[str, Any]],
) -> list[AuctionRow]:
    if not property_rows:
        return [normalize_select_org_notice(notice, detail, property_rows)]

    detail_item = _first_item(detail)
    property_name = str(detail_item.get("propertyName") or notice.get("propertyName") or "")
    common_values = [
        0,
        format_millis_date(notice.get("lastUpdated") or detail_item.get("lastUpdated") or detail_item.get("genDate")),
        "",
        detail_item.get("ownerFullname") or "",
        detail_item.get("addrOwner") or detail_item.get("ownerAddress") or "",
        "",
        "",
        0,
        format_millis_date(detail_item.get("fromDate") or notice.get("receiveTimeStart")),
        format_millis_date(detail_item.get("toDate") or notice.get("receiveTimeEnd")),
        detail_item.get("addressReceive") or "",
        detail_item.get("contactInfo") or "",
        build_select_org_detail_url(notice.get("id"), property_name),
    ]

    rows: list[AuctionRow] = []
    for property_row in property_rows:
        values = list(common_values)
        values[2] = _property_display_name(property_row)
        values[5] = str(property_row.get("propertyAmount") or "").strip()
        values[6] = str(property_row.get("propertyQuality") or "").strip()
        values[7] = _number(property_row.get("propertyStartPrice"))
        rows.append(AuctionRow(sheet_name="", values=values))
    return rows


def normalize_select_org_result_notices(
    notice: Mapping[str, Any],
    owner: Mapping[str, Any],
    history: Mapping[str, Any],
    info: Mapping[str, Any],
) -> list[AuctionRow]:
    property_rows = history.get("property") or []
    if not property_rows:
        property_rows = [
            {
                "propertyName": info.get("subPropertyName") or notice.get("propertyName"),
                "propertyAmount": "",
                "propertyQuality": "",
                "propertyStartPrice": 0,
            }
        ]

    org_info = history.get("orgInfo") or {}
    property_name_for_url = str(info.get("subPropertyName") or notice.get("propertyName") or "")
    common_values = [
        0,
        format_millis_date(notice.get("publishTime") or info.get("publishTime") or notice.get("lastUpdated")),
        "",
        owner.get("fullname") or notice.get("ownerFullname") or "",
        owner.get("addrOwner") or notice.get("ownerAddress") or "",
        "",
        "",
        0,
        org_info.get("orgName") or notice.get("orgName") or "",
        org_info.get("addrFull") or notice.get("orgSelectedAddr") or "",
        org_info.get("foneNumber") or notice.get("orgSelectedTel") or "",
        build_select_org_result_detail_url(notice.get("id"), property_name_for_url),
    ]

    rows: list[AuctionRow] = []
    for property_row in property_rows:
        values = list(common_values)
        values[2] = _property_display_name(property_row) or property_name_for_url
        values[5] = str(property_row.get("propertyAmount") or "").strip()
        values[6] = str(property_row.get("propertyQuality") or "").strip()
        values[7] = _number(property_row.get("propertyStartPrice"))
        rows.append(AuctionRow(sheet_name="", values=values))
    return rows


def extract_province(text: str) -> str:
    for province in PROVINCES:
        if re.search(rf"(?<!\w){re.escape(province)}(?!\w)", text or "", flags=re.IGNORECASE):
            return "Tây Ninh" if province == "Tây Ninh" else province
    return "Toàn quốc"


def _number(value: Any) -> int | float:
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return value
    cleaned = re.sub(r"[^\d.-]", "", str(value))
    if cleaned in ("", "-", "."):
        return 0
    number = float(cleaned)
    return int(number) if number.is_integer() else number


def _merge_deposit(items: list[Mapping[str, Any]], deposit_unit: Any) -> int | float:
    values = [_number(item.get("deposit")) for item in items]
    if _number(deposit_unit) == 1:
        return max(values, default=0)
    return sum(values)


def _format_deposit(value: Any, deposit_unit: Any) -> int | float | str:
    numeric = _number(value)
    if _number(deposit_unit) == 1:
        return f"{numeric:g}%"
    return numeric


def _auction_property_rows(notice: Mapping[str, Any], detail: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = detail.get("items") or []
    if rows:
        return list(rows)
    return [
        {
            "propertyName": notice.get("propertyName") or "",
            "propertyAmount": detail.get("propertyAmount") or "",
            "propertyPlace": detail.get("propertyPlace") or "",
            "propertyStartPrice": detail.get("propertyStartPrice"),
            "deposit": detail.get("deposit"),
            "depositUnit": detail.get("depositUnit"),
            "detail": detail.get("detail") or detail.get("propertyQuality") or "",
        }
    ]


def _auction_property_name(property_row: Mapping[str, Any], fallback: str, notice: Mapping[str, Any]) -> str:
    name = str(property_row.get("propertyName") or "").strip()
    if name:
        return name
    return f"{notice.get('titleName') or ''}{fallback}".strip()


def _auction_info(notice: Mapping[str, Any]) -> str:
    parts = []
    for label, field_name in (
        ("Thời gian đấu giá", "auctionTime"),
        ("Địa điểm đấu giá", "auctionPlace"),
        ("Hình thức đấu giá", "auctionForm"),
        ("Phương thức đấu giá", "auctionMethod"),
    ):
        value = notice.get(field_name)
        if field_name == "auctionTime":
            value = format_millis_date(value)
        if value not in (None, ""):
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


def _auction_registration_info(notice: Mapping[str, Any]) -> str:
    for field_name in ("aucRegPlace", "auctionRegPlace", "regPlace", "registerPlace"):
        value = notice.get(field_name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _first_present(source: Mapping[str, Any], *field_names: str) -> Any:
    for field_name in field_names:
        value = source.get(field_name)
        if value not in (None, ""):
            return value
    return ""


def _first_item(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    items = detail.get("items") or []
    if items:
        return items[0]
    return detail


def _join_property_names(property_rows: list[Mapping[str, Any]]) -> str:
    values: list[str] = []
    for row in property_rows:
        display_name = _property_display_name(row)
        if display_name:
            values.append(display_name)
    return "\n".join(values)


def _property_display_name(property_row: Mapping[str, Any]) -> str:
    property_type = str(property_row.get("propertyTypeName") or "").strip()
    property_name = str(property_row.get("propertyName") or "").strip()
    if property_type and property_name:
        return f"{property_type} - {property_name}"
    return property_name or property_type


def _join_property_field(property_rows: list[Mapping[str, Any]], field_name: str) -> str:
    return "\n".join(str(row.get(field_name) or "").strip() for row in property_rows if row.get(field_name) not in (None, ""))
