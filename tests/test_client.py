from dgts_crawler.client import DGTSCrawlerClient
from dgts_crawler.runner import AuctionFilters


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeResponse(self.payloads.pop(0))


def test_search_page_uses_full_auction_filter_params_like_website():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession([{"items": []}])
    filters = AuctionFilters(
        selected_organization_id="2063",
        full_name="Người có tài sản",
        start_date="01/06/2026",
        end_date="05/06/2026",
        start_publish_date="02/06/2026",
        end_publish_date="06/06/2026",
        province_id="109849",
        district_id="110197",
        from_first_price="1,000,000",
        to_first_price="2 000 000",
        property_type_id="173",
        type_order="1",
    )

    client.search_page(2, 10, filters=filters)

    call = client.session.calls[0]
    assert call["url"].endswith("/portal/search/auction-notice")
    assert call["params"] == {
        "assetName": "",
        "endDate": "05/06/2026",
        "endPublishDate": "06/06/2026",
        "fromFirstPrice": "1000000",
        "fullName": "Người có tài sản",
        "numberPerPage": 10,
        "p": 2,
        "propertyTypeId": "173",
        "provinceId": "109849",
        "districtId": "110197",
        "searchSimple": "",
        "selectedOrganizationId": "2063",
        "startDate": "01/06/2026",
        "startPublishDate": "02/06/2026",
        "toFirstPrice": "2000000",
        "typeOrder": "1",
    }


def test_auction_dropdown_methods_use_website_endpoints():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            [{"id": 1, "name": "Tỉnh A"}],
            [{"id": 173, "name": "Đất"}],
            [{"id": 2063, "fullname": "Tổ chức A"}],
            [{"id": 110197, "name": "Quận A"}],
            [{"id": 2063, "fullname": "Tổ chức A"}],
        ]
    )

    assert client.list_provinces() == [{"id": 1, "name": "Tỉnh A"}]
    assert client.list_property_types() == [{"id": 173, "name": "Đất"}]
    assert client.list_auction_orgs() == [{"id": 2063, "fullname": "Tổ chức A"}]
    assert client.list_districts("109849") == [{"id": 110197, "name": "Quận A"}]
    assert client.list_orgs_by_city("109849") == [{"id": 2063, "fullname": "Tổ chức A"}]

    assert client.session.calls[0]["url"].endswith("/common/getListProvince")
    assert client.session.calls[0]["params"] == {}
    assert client.session.calls[1]["url"].endswith("/common/getListPropertyType")
    assert client.session.calls[1]["params"] == {}
    assert client.session.calls[2]["url"].endswith("/portal/getListOrgTtTcCn")
    assert client.session.calls[2]["params"] == {}
    assert client.session.calls[3]["url"].endswith("/common/getListDistrict")
    assert client.session.calls[3]["params"] == {"province": "109849"}
    assert client.session.calls[4]["url"].endswith("/common/getOrgByCityID")
    assert client.session.calls[4]["params"] == {"cityID": "109849"}


def test_search_select_org_page_uses_select_org_endpoint_without_broken_publish_date_params():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession([{"items": []}])

    client.search_select_org_page(2, 10, "01/06/2026", "05/06/2026")

    call = client.session.calls[0]
    assert call["url"].endswith("/ThongTin/getInfoSelectAuctionOrg")
    assert call["params"] == {
        "p": 2,
        "numberPerPage": 10,
    }


def test_iter_select_org_notices_paginates_deduplicates_and_respects_max_pages():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {
                "items": [{"id": 1}, {"id": 2}],
                "pageCount": 3,
            },
            {
                "items": [{"id": 2}, {"id": 3}],
                "pageCount": 3,
            },
        ]
    )

    notices = list(client.iter_select_org_notices(page_size=2, max_pages=2))

    assert notices == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [call["params"]["p"] for call in client.session.calls] == [1, 2]


def test_iter_select_org_notices_filters_by_public_date_before_detail_fetch():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {
                "items": [
                    {"id": 1, "lastUpdated": 1780729740000},
                    {"id": 2, "lastUpdated": 1780680555000},
                    {"id": 3, "lastUpdated": 1780594155000},
                ],
                "pageCount": 3,
            }
        ]
    )

    notices = list(client.iter_select_org_notices(start_date="06/06/2026", end_date="06/06/2026", page_size=10))

    assert notices == [{"id": 1, "lastUpdated": 1780729740000}, {"id": 2, "lastUpdated": 1780680555000}]
    assert [call["params"]["p"] for call in client.session.calls] == [1]


def test_select_org_detail_and_property_info_use_detail_endpoints():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {"items": [{"id": 105862}]},
            [{"propertyName": "Tài sản A"}],
        ]
    )

    assert client.select_org_detail(105862) == {"items": [{"id": 105862}]}
    assert client.select_org_property_info(105862) == [{"propertyName": "Tài sản A"}]

    assert client.session.calls[0]["url"].endswith("/ThongTin/getDetailSelectOrgAuction")
    assert client.session.calls[0]["params"] == {"id": 105862}
    assert client.session.calls[1]["url"].endswith("/ThongTin/getDetailPropertyInfo")
    assert client.session.calls[1]["params"] == {"noticeID": 105862}


def test_search_select_org_result_page_uses_result_endpoint_without_date_params():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession([{"items": []}])

    client.search_select_org_result_page(2, 10, "01/06/2026", "05/06/2026")

    call = client.session.calls[0]
    assert call["url"].endswith("/ThongTin/getResultSelectAuctionOrg")
    assert call["params"] == {
        "p": 2,
        "numberPerPage": 10,
    }


def test_iter_select_org_result_notices_paginates_deduplicates_and_respects_max_pages():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {"items": [{"id": 1}, {"id": 2}], "pageCount": 3},
            {"items": [{"id": 2}, {"id": 3}], "pageCount": 3},
        ]
    )

    notices = list(client.iter_select_org_result_notices(page_size=2, max_pages=2))

    assert notices == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert [call["params"]["p"] for call in client.session.calls] == [1, 2]


def test_iter_select_org_result_notices_filters_by_public_date_before_detail_fetch():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {
                "items": [
                    {"id": 1, "publishTime": 1780738428000},
                    {"id": 2, "publishTime": 1780680555000},
                    {"id": 3, "publishTime": 1780594155000},
                ],
                "pageCount": 3,
            }
        ]
    )

    notices = list(client.iter_select_org_result_notices(start_date="06/06/2026", end_date="06/06/2026", page_size=10))

    assert notices == [{"id": 1, "publishTime": 1780738428000}, {"id": 2, "publishTime": 1780680555000}]
    assert [call["params"]["p"] for call in client.session.calls] == [1]


def test_select_org_result_detail_methods_use_result_detail_endpoints():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {"fullname": "Cơ quan A"},
            {"orgInfo": {"orgName": "Tổ chức A"}},
            {"publishTime": 1780663701000},
        ]
    )

    assert client.select_org_result_owner(25941) == {"fullname": "Cơ quan A"}
    assert client.select_org_result_history(25941) == {"orgInfo": {"orgName": "Tổ chức A"}}
    assert client.select_org_result_info(25941) == {"publishTime": 1780663701000}

    assert client.session.calls[0]["url"].endswith("/ThongTin/getInfoOwner")
    assert client.session.calls[0]["params"] == {"id": 25941}
    assert client.session.calls[1]["url"].endswith("/ThongTin/getDetaiResultHistory")
    assert client.session.calls[1]["params"] == {"resultID": 25941}
    assert client.session.calls[2]["url"].endswith("/ThongTin/getInfoResult")
    assert client.session.calls[2]["params"] == {"resultID": 25941}
