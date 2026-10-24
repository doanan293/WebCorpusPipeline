from __future__ import annotations

import hashlib
import re

# Longest names first so "tháng mười hai" is not read as "tháng mười".
_MONTHS = re.compile(
    r"\b(?:tháng (?:mười một|mười hai|một|hai|ba|tư|năm|sáu|bảy|tám|chín|mười|giêng|chạp)"
    r"|january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b"
)
_DIGITS = re.compile(r"\d+")
_COUNTER_LINE = re.compile(r"^(?:lượt xem|lượt tải|viewed|views)\b")


def change_fingerprint(text: str) -> str:
    """Hash text with digits, month names and view counters normalised away.

    Two versions of a page with the same fingerprint differ only in dates or counters, which is
    not a content change. ``content_id`` stays the exact hash used for deduplication.
    """
    lines: list[str] = []
    for raw_line in text.casefold().splitlines():
        line = " ".join(raw_line.split())
        if not line or _COUNTER_LINE.match(line):
            continue
        lines.append(_DIGITS.sub("0", _MONTHS.sub("<month>", line)))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
