from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Iterator

import requests

from .transform import merge_detail
from .utils import format_millis_date


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
