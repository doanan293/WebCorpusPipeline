"""What the blocks of the slide send each other: status strings and the record of ``/add``."""

from __future__ import annotations

from typing import Any

from crawler.domain.shared.change import UNCHANGED

IDLE = "đang chờ"
BUSY = "đang bận"
ACCEPTED = "đã nhận"

_TEXT_FIELDS = ("url", "format", "title", "content")
_PAIR = 2


def validate_record(value: object) -> dict[str, Any] | str:
    """The record with ``out-links`` as ``(url, text)`` pairs, or why it is not one.

    Unknown keys are kept: a block takes the fields it knows and ignores the rest. Two short
    notices of a recrawl pass as they are: a 304 (``format: not_modified``, reader to merger) and
    an unchanged visit (``change: unchanged`` or ``not_modified``, merger to storage).
    """
    if not isinstance(value, dict):
        return "body must be a JSON object"
    record: dict[str, Any] = dict(value)
    if record.get("format") == "not_modified" or record.get("change") in UNCHANGED:
        if not isinstance(record.get("url"), str):
            return "url must be a string"
        return record if record["url"] else "url must not be empty"
    for name in _TEXT_FIELDS:
        if not isinstance(record.get(name), str):
            return f"{name} must be a string"
    if not record["url"]:
        return "url must not be empty"
    links = record.get("out-links")
    if not isinstance(links, list):
        return "out-links must be a list of [url, text] pairs"
    pairs: list[tuple[str, str]] = []
    for link in links:
        pair = _pair(link)
        if pair is None:
            return "out-links must be a list of [url, text] pairs"
        pairs.append(pair)
    record["out-links"] = pairs
    return record


def _pair(link: object) -> tuple[str, str] | None:
    if not isinstance(link, list) or len(link) != _PAIR:
        return None
    url, text = link
    if isinstance(url, str) and isinstance(text, str):
        return url, text
    return None
