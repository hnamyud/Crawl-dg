from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Any


BASE_URL = "https://dgts.moj.gov.vn"
VIETNAM_TZ = timezone(timedelta(hours=7))


def format_millis_date(value: Any) -> str:
    if value in (None, ""):
        return ""
    timestamp = int(value) / 1000
    return datetime.fromtimestamp(timestamp, tz=VIETNAM_TZ).strftime("%d/%m/%Y")


def make_notice_code(notice_id: Any) -> str:
    return f"TS_{notice_id}"


def slugify_vietnamese(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    ascii_text = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    ascii_text = ascii_text.replace("đ", "d").replace("Đ", "D")
    ascii_text = ascii_text.lower()
    ascii_text = re.sub(r"[^0-9a-z\s-]", " ", ascii_text)
    ascii_text = re.sub(r"[\s-]+", "-", ascii_text).strip("-")
    return ascii_text or "thong-bao"


def build_detail_url(notice_id: Any, property_name: str) -> str:
    return (
        f"{BASE_URL}/thong-bao-cong-khai-viec-dau-gia/"
        f"{slugify_vietnamese(property_name)}-{notice_id}.html"
    )


def build_select_org_detail_url(notice_id: Any, property_name: str) -> str:
    return (
        f"{BASE_URL}/thong-bao-lua-chon-to-chuc-dau-gia/"
        f"{slugify_vietnamese(property_name)}-{notice_id}.html"
    )


def build_select_org_result_detail_url(notice_id: Any, property_name: str) -> str:
    return (
        f"{BASE_URL}/thong-bao-ket-qua-lua-chon-to-chuc-dau-gia/"
        f"{slugify_vietnamese(property_name)}-{notice_id}.html"
    )
