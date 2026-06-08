from dgts_crawler.client import DGTSCrawlerClient, _auction_detail_info_from_html
from dgts_crawler.runner import AuctionFilters


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.text = payload if isinstance(payload, str) else ""

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


def test_auction_detail_html_parser_extracts_server_rendered_fields():
    html = """
    <h6>Thông tin người có tài sản</h6>
    <p>Tên người có tài sản:</p><p>Ngân hàng TMCP Tiên Phong</p>
    <p>Địa chỉ:</p><p>Tòa nhà TPBank, số 57 phố Lý Thường Kiệt, Hà Nội.</p>
    <h6>Thông tin đơn vị tổ chức hành nghề đấu giá</h6>
    <p>Tên đơn vị Tổ chức đấu giá:</p><p>Công ty đấu giá hợp danh Rồng Việt</p>
    <p>Địa chỉ:</p><p>Số 51 Phạm Văn Bạch, thành phố Đà Nẵng</p>
    <p>Số điện thoại:</p><p>0905156237</p>
    <h6>Thông tin việc đấu giá</h6>
    <p>Thời gian tổ chức cuộc đấu giá:</p><p><b>09:30 16/06/2026</b></p>
    <p>Địa điểm tổ chức cuộc đấu gá:</p><p><b>Website đấu giá trực tuyến daugiarongviet.vn</b></p>
    <p>Thời gian bắt đầu đăng ký tham gia đấu giá:</p><p><b>08:00 04/06/2026</b></p>
    <p>Thời gian kết thúc đăng ký tham gia đấu giá:</p><p><b>17:00 11/06/2026</b></p>
    <p>Địa điểm, điều kiện, cách thức đăng ký:</p><p><b>đăng ký trực tuyến tại website</b></p>
    <p>Thời gian bắt đầu nộp tiền đặt trước:</p><p><b>08:00 04/06/2026</b></p>
    <p>Thời gian kết thúc nộp tiền đặt trước:</p><p><b>17:00 11/06/2026</b></p>
    """

    info = _auction_detail_info_from_html(html)

    assert info["ownerName"] == "Ngân hàng TMCP Tiên Phong"
    assert info["ownerAddress"].startswith("Tòa nhà TPBank")
    assert info["orgName"] == "Công ty đấu giá hợp danh Rồng Việt"
    assert info["orgPhone"] == "0905156237"
    assert info["auctionTimeText"] == "09:30 16/06/2026"
    assert info["auctionPlace"] == "Website đấu giá trực tuyến daugiarongviet.vn"
    assert info["registrationStartText"] == "08:00 04/06/2026"
    assert info["registrationInfo"] == "đăng ký trực tuyến tại website"
    assert info["depositEndText"] == "17:00 11/06/2026"


def test_auction_detail_merges_property_api_view_api_and_html_fallback():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession(
        [
            {"items": [{"propertyName": "Xe", "propertyStartPrice": 100, "deposit": 10}]},
            {"listFile": [{"fileName": "Quy chế.pdf"}]},
            """
            <h6>Thông tin người có tài sản</h6>
            <p>Tên người có tài sản:</p><p>Ngân hàng A</p>
            <p>Địa chỉ:</p><p>Địa chỉ A</p>
            <h6>Thông tin đơn vị tổ chức hành nghề đấu giá</h6>
            <p>Tên đơn vị Tổ chức đấu giá:</p><p>Tổ chức B</p>
            <p>Địa chỉ:</p><p>Địa chỉ B</p>
            <h6>Thông tin việc đấu giá</h6>
            <p>Thời gian tổ chức cuộc đấu giá:</p><p>09:00 10/06/2026</p>
            """,
        ]
    )

    detail = client.auction_detail({"id": 1, "propertyName": "Xe"})

    assert detail["items"][0]["propertyName"] == "Xe"
    assert detail["attachmentNames"] == "Quy chế.pdf"
    assert detail["ownerName"] == "Ngân hàng A"
    assert detail["orgName"] == "Tổ chức B"
    assert detail["auctionTimeText"] == "09:00 10/06/2026"
    assert client.session.calls[1]["url"].endswith("/portal/viewDetailAuctionInfo")
    assert client.session.calls[2]["url"].endswith("-1.html")


def test_search_select_org_page_uses_select_org_endpoint_without_broken_publish_date_params():
    client = DGTSCrawlerClient(sleep_seconds=0)
    client.session = FakeSession([{"items": []}])

    client.search_select_org_page(2, 10, "01/06/2026", "05/06/2026")

    call = client.session.calls[0]
    assert call["url"].endswith("/ThongTin/getInfoSelectAuctionOrg")
    assert call["params"] == {
        "p": 2,
        "numberPerPage": 10,
        "ownerFullname": "",
        "startDate": "01/06/2026",
        "endDate": "05/06/2026",
        "startPublishDate": "",
        "endPublishDate": "",
        "province": "",
        "district": "",
        "propertyTypeId": "",
        "noticeSub": "",
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
        "ownerFullname": "",
        "orgID": "",
        "province": "",
        "district": "",
        "propertyTypeId": "",
        "noticeSub": "",
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

    notices = list(
        client.iter_select_org_result_notices(
            start_date="06/06/2026",
            end_date="06/06/2026",
            page_size=10,
            max_pages=1,
        )
    )

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
