from __future__ import annotations

import html as html_lib
import re
import time
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Iterator

import requests

from .transform import merge_detail
from .utils import build_detail_url, format_millis_date


BASE_URL = "https://dgts.moj.gov.vn"


class DGTSCrawlerClient:
    def __init__(self, timeout: int = 30, sleep_seconds: float = 0.2) -> None:
        self.timeout = timeout
        self.sleep_seconds = sleep_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; DGTSCrawler/0.1)",
                "Referer": f"{BASE_URL}/thong-bao-cong-khai-viec-dau-gia.html",
                "Accept": "application/json, text/plain, */*",
            }
        )

    def search_page(
        self,
        page: int,
        page_size: int,
        start_date: str = "",
        end_date: str = "",
        filters: Any | None = None,
    ) -> dict[str, Any]:
        params = _auction_search_params(page, page_size, start_date, end_date, filters)
        return self._get_json("/portal/search/auction-notice", params)

    def list_provinces(self) -> list[dict[str, Any]]:
        payload = self._get_json("/common/getListProvince", {})
        return payload if isinstance(payload, list) else []

    def list_property_types(self) -> list[dict[str, Any]]:
        payload = self._get_json("/common/getListPropertyType", {})
        return payload if isinstance(payload, list) else []

    def list_auction_orgs(self) -> list[dict[str, Any]]:
        payload = self._get_json("/portal/getListOrgTtTcCn", {})
        return payload if isinstance(payload, list) else []

    def list_districts(self, province_id: Any) -> list[dict[str, Any]]:
        payload = self._get_json("/common/getListDistrict", {"province": province_id})
        return payload if isinstance(payload, list) else []

    def list_orgs_by_city(self, province_id: Any) -> list[dict[str, Any]]:
        payload = self._get_json("/common/getOrgByCityID", {"cityID": province_id})
        return payload if isinstance(payload, list) else []

    def search_select_org_page(
        self,
        page: int,
        page_size: int,
        start_date: str = "",
        end_date: str = "",
        filters: Any | None = None,
    ) -> dict[str, Any]:
        params = _select_org_search_params(page, page_size, start_date, end_date, filters)
        return self._get_json("/ThongTin/getInfoSelectAuctionOrg", params)

    def search_select_org_result_page(
        self,
        page: int,
        page_size: int,
        start_date: str = "",
        end_date: str = "",
        filters: Any | None = None,
    ) -> dict[str, Any]:
        params = _select_org_result_search_params(page, page_size, filters)
        return self._get_json("/ThongTin/getResultSelectAuctionOrg", params)

    def iter_notices(
        self,
        start_date: str = "",
        end_date: str = "",
        page_size: int = 100,
        max_pages: int | None = None,
        filters: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        page = 1
        seen: set[Any] = set()
        while True:
            payload = self.search_page(page, page_size, start_date, end_date, filters)
            for item in payload.get("items") or []:
                notice_id = item.get("id")
                if notice_id in seen:
                    continue
                seen.add(notice_id)
                yield item

            page_count = int(payload.get("pageCount") or page)
            if page >= page_count:
                break
            if max_pages is not None and page >= max_pages:
                break
            page += 1
            time.sleep(self.sleep_seconds)

    def iter_select_org_notices(
        self,
        start_date: str = "",
        end_date: str = "",
        page_size: int = 100,
        max_pages: int | None = None,
        filters: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        page = 1
        seen: set[Any] = set()
        while True:
            payload = self.search_select_org_page(page, page_size, start_date, end_date, filters)
            should_stop_by_date = False
            for item in payload.get("items") or []:
                notice_id = item.get("id")
                if notice_id in seen:
                    continue
                date_state = _date_filter_state(item.get("lastUpdated"), start_date, end_date)
                if date_state == "newer":
                    continue
                if date_state == "older":
                    should_stop_by_date = True
                    break
                seen.add(notice_id)
                yield item

            page_count = int(payload.get("pageCount") or page)
            if should_stop_by_date:
                break
            if page >= page_count:
                break
            if max_pages is not None and page >= max_pages:
                break
            page += 1
            time.sleep(self.sleep_seconds)

    def iter_select_org_result_notices(
        self,
        start_date: str = "",
        end_date: str = "",
        page_size: int = 100,
        max_pages: int | None = None,
        filters: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        # publishTime is not sorted on the server, so we cannot early-stop.
        # We do a client-side filter on publishTime across all pages up to max_pages.
        publish_start = _filter_value(filters, "publish_start_date") or start_date
        publish_end = _filter_value(filters, "publish_end_date") or end_date
        page = 1
        seen: set[Any] = set()
        while True:
            payload = self.search_select_org_result_page(page, page_size, start_date, end_date, filters)
            for item in payload.get("items") or []:
                notice_id = item.get("id")
                if notice_id in seen:
                    continue
                date_state = _date_filter_state(item.get("publishTime"), publish_start, publish_end)
                if date_state in ("newer", "older"):
                    continue
                seen.add(notice_id)
                yield item

            page_count = int(payload.get("pageCount") or page)
            if page >= page_count:
                break
            if max_pages is not None and page >= max_pages:
                break
            page += 1
            time.sleep(self.sleep_seconds)

    def property_detail(self, auction_info_id: Any) -> dict[str, Any]:
        payload = self._get_json("/portal/propertyInfo", {"auctionInfoId": auction_info_id})
        return merge_detail(payload)

    def auction_detail(self, notice: dict[str, Any]) -> dict[str, Any]:
        notice_id = notice.get("id")
        detail = self.property_detail(notice_id)
        view_detail = _safe_dict(lambda: self._get_json("/portal/viewDetailAuctionInfo", {"auctionInfoId": notice_id}))
        html_detail = _safe_dict(lambda: self.auction_detail_page_info(notice))
        if view_detail:
            detail["viewDetailAuctionInfo"] = view_detail
            _merge_non_empty_detail_fields(detail, _auction_view_detail_fields(view_detail))
        if html_detail:
            detail["htmlDetail"] = html_detail
            _merge_non_empty_detail_fields(detail, html_detail)
        return detail

    def auction_detail_page_info(self, notice: dict[str, Any]) -> dict[str, str]:
        url = build_detail_url(notice.get("id"), str(notice.get("propertyName") or ""))
        html_text = self._get_text(url)
        return _auction_detail_info_from_html(html_text)

    def select_org_detail(self, notice_id: Any) -> dict[str, Any]:
        return self._get_json("/ThongTin/getDetailSelectOrgAuction", {"id": notice_id})

    def select_org_property_info(self, notice_id: Any) -> list[dict[str, Any]]:
        payload = self._get_json("/ThongTin/getDetailPropertyInfo", {"noticeID": notice_id})
        return payload if isinstance(payload, list) else []

    def select_org_result_owner(self, result_id: Any) -> dict[str, Any]:
        return self._get_json("/ThongTin/getInfoOwner", {"id": result_id})

    def select_org_result_history(self, result_id: Any) -> dict[str, Any]:
        return self._get_json("/ThongTin/getDetaiResultHistory", {"resultID": result_id})

    def select_org_result_info(self, result_id: Any) -> dict[str, Any]:
        return self._get_json("/ThongTin/getInfoResult", {"resultID": result_id})

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
        raise RuntimeError(f"DGTS request failed for {url}: {last_error}") from last_error

    def _get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=self.timeout)
                response.raise_for_status()
                return response.text
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
        raise RuntimeError(f"DGTS request failed for {url}: {last_error}") from last_error


def _safe_dict(loader: Any) -> dict[str, Any]:
    try:
        value = loader()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _merge_non_empty_detail_fields(detail: dict[str, Any], fields: dict[str, Any]) -> None:
    for key, value in fields.items():
        if value not in (None, ""):
            detail[key] = value


def _auction_view_detail_fields(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        "ownerName": detail.get("fullName"),
        "ownerAddress": detail.get("addrOwner"),
        "orgName": detail.get("orgFullName"),
        "orgAddress": detail.get("orgAddress"),
        "orgPhone": detail.get("foneNumber"),
        "auctionTimeText": detail.get("strAuctionTime"),
        "auctionPlace": detail.get("aucAddr"),
        "auctionMethod": detail.get("strAuctionMethod") or detail.get("aucMethod"),
        "registrationStartText": detail.get("strAucRegTimeStart"),
        "registrationEndText": detail.get("strAucRegTimeEnd"),
        "registrationInfo": detail.get("aucCondition"),
        "depositStartText": detail.get("strAucTimeDepositStart"),
        "depositEndText": detail.get("strAucTimeDepositEnd"),
        "attachmentNames": "\n".join(
            str(item.get("fileName") or "").strip()
            for item in (detail.get("listFile") or [])
            if isinstance(item, dict) and str(item.get("fileName") or "").strip()
        ),
    }


class _TextCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = _clean_text(data)
        if value:
            self.parts.append(value)


def _auction_detail_info_from_html(html_text: str) -> dict[str, str]:
    parser = _TextCollector()
    parser.feed(html_text)
    tokens = [token for token in parser.parts if not _is_template_text(token)]
    values: dict[str, str] = {}

    owner_at = _find_token(tokens, "Thông tin người có tài sản")
    org_at = _find_token(tokens, "Thông tin đơn vị tổ chức hành nghề đấu giá")
    auction_at = _find_token(tokens, "Thông tin việc đấu giá")
    if owner_at >= 0:
        values["ownerName"] = _value_after(tokens, "Tên người có tài sản:", owner_at, org_at)
        values["ownerAddress"] = _value_after(tokens, "Địa chỉ:", owner_at, org_at)
    if org_at >= 0:
        values["orgName"] = _value_after(tokens, "Tên đơn vị Tổ chức đấu giá:", org_at, auction_at)
        values["orgAddress"] = _value_after(tokens, "Địa chỉ:", org_at, auction_at)
        values["orgPhone"] = _value_after(tokens, "Số điện thoại:", org_at, auction_at)
    if auction_at >= 0:
        values["auctionTimeText"] = _value_after(tokens, "Thời gian tổ chức cuộc đấu giá:", auction_at, -1)
        values["auctionPlace"] = (
            _value_after(tokens, "Địa điểm tổ chức cuộc đấu giá:", auction_at, -1)
            or _value_after(tokens, "Địa điểm tổ chức cuộc đấu gá:", auction_at, -1)
        )
        values["registrationStartText"] = _value_after(tokens, "Thời gian bắt đầu đăng ký tham gia đấu giá:", auction_at, -1)
        values["registrationEndText"] = _value_after(tokens, "Thời gian kết thúc đăng ký tham gia đấu giá:", auction_at, -1)
        values["registrationInfo"] = _value_after(tokens, "Địa điểm, điều kiện, cách thức đăng ký:", auction_at, -1)
        values["depositStartText"] = _value_after(tokens, "Thời gian bắt đầu nộp tiền đặt trước:", auction_at, -1)
        values["depositEndText"] = _value_after(tokens, "Thời gian kết thúc nộp tiền đặt trước:", auction_at, -1)
        values["attachmentNames"] = _attachments_after(tokens, auction_at)
    return {key: value for key, value in values.items() if value}


def _find_token(tokens: list[str], expected: str, start: int = 0) -> int:
    expected_key = _label_key(expected)
    for index in range(max(start, 0), len(tokens)):
        if _label_key(tokens[index]) == expected_key:
            return index
    return -1


def _value_after(tokens: list[str], label: str, start: int = 0, end: int = -1) -> str:
    label_at = _find_token(tokens, label, start)
    if label_at < 0:
        return ""
    stop = end if end >= 0 else len(tokens)
    if label_at >= stop:
        return ""
    for value in tokens[label_at + 1 : stop]:
        if _looks_like_label(value):
            return ""
        if value:
            return value
    return ""


def _attachments_after(tokens: list[str], start: int) -> str:
    file_at = _find_token(tokens, "File đính kèm:", start)
    if file_at < 0:
        return ""
    names = []
    for value in tokens[file_at + 1 :]:
        if value in {"Quay lại"} or value.startswith("var "):
            break
        if value and not _looks_like_label(value):
            names.append(value)
    return "\n".join(names)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(value or "")).strip()


def _label_key(value: str) -> str:
    return _clean_text(value).lower().rstrip(":")


def _looks_like_label(value: str) -> bool:
    return _clean_text(value).endswith(":")


def _is_template_text(value: str) -> bool:
    stripped = _clean_text(value)
    return "{{" in stripped or "}}" in stripped


def _date_filter_state(value: Any, start_date: str, end_date: str) -> str:
    if not start_date and not end_date:
        return "in-range"
    if value in (None, ""):
        return "in-range"
    public_date = datetime.strptime(format_millis_date(value), "%d/%m/%Y")
    if end_date and public_date > datetime.strptime(end_date, "%d/%m/%Y"):
        return "newer"
    if start_date and public_date < datetime.strptime(start_date, "%d/%m/%Y"):
        return "older"
    return "in-range"


def _select_org_result_search_params(page: int, page_size: int, filters: Any | None) -> dict[str, Any]:
    return {
        "p": page,
        "numberPerPage": page_size,
        "ownerFullname": _filter_value(filters, "owner_fullname"),
        "orgID": _filter_value(filters, "org_id"),
        "province": _filter_value(filters, "province_id"),
        "district": _filter_value(filters, "district_id"),
        "propertyTypeId": _filter_value(filters, "property_type_id"),
        "noticeSub": _filter_value(filters, "notice_sub"),
    }


def _select_org_search_params(page: int, page_size: int, start_date: str, end_date: str, filters: Any | None) -> dict[str, Any]:
    return {
        "p": page,
        "numberPerPage": page_size,
        "ownerFullname": _filter_value(filters, "owner_fullname"),
        "startDate": _filter_value(filters, "start_date") or start_date,
        "endDate": _filter_value(filters, "end_date") or end_date,
        "startPublishDate": _filter_value(filters, "start_publish_date"),
        "endPublishDate": _filter_value(filters, "end_publish_date"),
        "province": _filter_value(filters, "province_id"),
        "district": _filter_value(filters, "district_id"),
        "propertyTypeId": _filter_value(filters, "property_type_id"),
        "noticeSub": _filter_value(filters, "notice_sub"),
    }


def _auction_search_params(page: int, page_size: int, start_date: str, end_date: str, filters: Any | None) -> dict[str, Any]:
    start_publish_date = _filter_value(filters, "start_publish_date") or start_date
    end_publish_date = _filter_value(filters, "end_publish_date") or end_date
    return {
        "assetName": _filter_value(filters, "asset_name"),
        "endDate": _filter_value(filters, "end_date"),
        "endPublishDate": end_publish_date,
        "fromFirstPrice": _normalize_price(_filter_value(filters, "from_first_price")),
        "fullName": _filter_value(filters, "full_name"),
        "numberPerPage": page_size,
        "p": page,
        "propertyTypeId": _filter_value(filters, "property_type_id"),
        "provinceId": _filter_value(filters, "province_id"),
        "districtId": _filter_value(filters, "district_id"),
        "searchSimple": _filter_value(filters, "search_simple"),
        "selectedOrganizationId": _filter_value(filters, "selected_organization_id"),
        "startDate": _filter_value(filters, "start_date"),
        "startPublishDate": start_publish_date,
        "toFirstPrice": _normalize_price(_filter_value(filters, "to_first_price")),
        "typeOrder": _filter_value(filters, "type_order") or "2",
    }


def _filter_value(filters: Any | None, field_name: str) -> str:
    if filters is None:
        return ""
    return str(getattr(filters, field_name, "") or "").strip()


def _normalize_price(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())
