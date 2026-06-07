from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping


SHEET_ORDER = [
    "Thanh lý",
    "Đất đai",
    "Ngân hàng",
    "Thi hành án",
    "Công an",
    "Khoáng sản",
    "Điện lực",
    "Công ty",
]

FIELD_WEIGHTS = {
    "fullname": 2.4,
    "propertyName": 1.8,
    "subPropertyName": 1.6,
    "propertyTypeName": 2.0,
    "org_name": 0.6,
    "propertyPlace": 0.4,
}

RULES = {
    "Ngân hàng": [
        (r"\bngan\s+hang\b", 10),
        (r"\b(vib|bidv|vietinbank|vietcombank|agribank|sacombank|mbbank|techcombank|vpbank|acb|shb|tpbank|hdbank)\b", 10),
        (r"\b(to\s+chuc\s+tin\s+dung|khoan\s+no|no\s+xau)\b", 7),
        (r"\btai\s+san\s+bao\s+dam\b", 3),
    ],
    "Thi hành án": [
        (r"\bthi\s+hanh\s+an\b", 10),
        (r"\bthads\b", 10),
        (r"\bch?i\s+cuc\s+thi\s+hanh\s+an\b", 10),
        (r"\bke\s+bien\b", 4),
    ],
    "Công an": [
        (r"\bcong\s+an\b", 10),
        (r"\bcanh\s+sat\b", 8),
        (r"\b(tang\s+vat|tich\s+thu|sung\s+(?:quy|cong)|vi\s+pham\s+hanh\s+chinh)\b", 7),
    ],
    "Khoáng sản": [
        (r"\b(khoang\s+san|quyen\s+khai\s+thac\s+khoang\s+san|mo\s+da|mo\s+cat|mo\s+than|than\s+da|tkv)\b", 10),
    ],
    "Điện lực": [
        (r"\b(dien\s+luc|evn|truyen\s+tai\s+dien|dien\s+mien|dien\s+ha\s+noi)\b", 10),
        (r"\b(tram\s+bien\s+ap|may\s+bien\s+ap|cap\s+dien|vat\s+tu\s+dien|cong\s+to\s+dien)\b", 8),
    ],
    "Công ty": [
        (r"\bcong\s+ty\b", 4),
        (r"\b(cty|tnhh|co\s+phan|cp|mtv|doanh\s+nghiep|hop\s+tac\s+xa)\b", 4),
        (r"\btai\s+san\s+thuoc\s+so\s+huu\s+cua\s+ca\s+nhan,\s+to\s+chuc\b", 5),
        (r"\btai\s+san\s+cua\s+doanh\s+nghiep\b", 5),
    ],
    "Đất đai": [
        (r"\bquyen\s+su\s+dung\s+dat\b", 9),
        (r"\bquyen\s+thue\s+dat\b", 9),
        (r"\bdat\s+(?:o|nong\s+nghiep|thuong\s+mai|co\s+so|san\s+xuat|trong\s+cay|nuoi\s+trong)\b", 5),
        (r"\b(thua\s+dat|to\s+ban\s+do|giay\s+chung\s+nhan\s+quyen\s+su\s+dung\s+dat)\b", 6),
    ],
    "Thanh lý": [
        (r"\b(thanh\s+ly|phat\s+mai|ban\s+thanh\s+ly)\b", 8),
        (r"\b(pha\s+do|thao\s+do|vat\s+lieu\s+thu\s+hoi|vat\s+lieu\s+thai|phe\s+lieu)\b", 8),
        (r"\b(cong\s+cu\s+dung\s+cu|tai\s+san\s+co\s+dinh|thiet\s+bi|may\s+moc|vat\s+tu|hang\s+hoa|ban\s+ghe)\b", 6),
        (r"\b(xe|o\s+to|oto|xe\s+tai|xe\s+may|phuong\s+tien|bien\s+kiem\s+soat)\b", 5),
    ],
}


def classify_notice(notice: Mapping[str, Any]) -> str:
    scores = score_notice(notice)
    best = max(SHEET_ORDER, key=lambda sheet: (scores[sheet], -SHEET_ORDER.index(sheet)))
    return best if scores[best] > 0 else "Thanh lý"


def score_notice(notice: Mapping[str, Any]) -> dict[str, float]:
    scores = {sheet: 0.0 for sheet in SHEET_ORDER}
    for field, field_weight in FIELD_WEIGHTS.items():
        text = _fold(str(notice.get(field) or ""))
        if not text:
            continue
        for sheet, rules in RULES.items():
            for pattern, weight in rules:
                if re.search(pattern, text):
                    scores[sheet] += weight * field_weight
    return scores


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    ascii_text = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    ascii_text = ascii_text.replace("đ", "d").replace("Đ", "D")
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()
