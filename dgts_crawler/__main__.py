from __future__ import annotations

import argparse
from pathlib import Path

from .runner import CrawlerConfig, run_crawl


def main() -> None:
    args = _parse_args()
    output, count = run_crawl(
        CrawlerConfig(
            from_date=args.from_date,
            to_date=args.to_date,
            max_pages=args.max_pages,
            page_size=args.page_size,
            detail_workers=args.detail_workers,
            crawl_all=args.all,
            notice_kind=args.notice_kind,
            output_path=Path(args.output),
            history_db_path=Path(args.history_db),
            enable_history=not args.no_history,
        )
    )
    print(f"Wrote {count} notices to {output}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl DGTS auction notices and export an Excel workbook.")
    parser.add_argument("--from", dest="from_date", default="", help="Start publish date, dd/MM/yyyy or yyyy-mm-dd.")
    parser.add_argument("--to", dest="to_date", default="", help="End publish date, dd/MM/yyyy or yyyy-mm-dd.")
    parser.add_argument("--max-pages", type=int, default=10, help="Maximum pages to crawl unless --all is set.")
    parser.add_argument("--page-size", type=int, default=10, help="Rows per page.")
    parser.add_argument("--detail-workers", type=int, default=1, help="Concurrent detail requests.")
    parser.add_argument("--all", action="store_true", help="Crawl every page matching the filters.")
    parser.add_argument(
        "--notice-kind",
        choices=["auction", "select-org", "select-org-result"],
        default="auction",
        help="Notice type to crawl: auction, select-org, or select-org-result.",
    )
    parser.add_argument("--output", default="DGTS_Output.xlsx", help="Output workbook path.")
    parser.add_argument("--history-db", default="outputs/dgts_history.sqlite", help="SQLite history database path.")
    parser.add_argument("--no-history", action="store_true", help="Disable SQLite history tracking.")
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
