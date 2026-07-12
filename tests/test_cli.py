from dgts_crawler.__main__ import _parse_args


def test_parse_args_defaults_to_auction_notice_kind():
    args = _parse_args([])

    assert args.notice_kind == "auction"


def test_parse_args_accepts_select_org_notice_kind():
    args = _parse_args(["--notice-kind", "select-org"])

    assert args.notice_kind == "select-org"


def test_parse_args_accepts_select_org_result_notice_kind():
    args = _parse_args(["--notice-kind", "select-org-result"])

    assert args.notice_kind == "select-org-result"


def test_parse_args_accepts_history_options():
    args = _parse_args(["--history-db", "outputs/history.sqlite", "--no-history"])

    assert args.history_db == "outputs/history.sqlite"
    assert args.no_history is True


def test_parse_args_accepts_detail_workers():
    args = _parse_args(["--detail-workers", "5"])

    assert args.detail_workers == 5


def test_parse_args_can_disable_timestamped_output():
    args = _parse_args(["--no-output-timestamp"])

    assert args.no_output_timestamp is True
