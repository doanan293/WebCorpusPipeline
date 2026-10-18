from __future__ import annotations

import pytest

from dieu_khien.domain.shared.protocol import ACCEPTED, BUSY, IDLE, validate_record


def test_slide_status_strings() -> None:
    assert (IDLE, BUSY, ACCEPTED) == ("đang chờ", "đang bận", "đã nhận")


def test_validate_record_accepts_slide_example() -> None:
    record = validate_record(
        {
            "url": "https://hust.edu.vn/tin-tuc1",
            "format": "html",
            "title": "Tin tức 1",
            "content": "Nội dung",
            "out-links": [["https://hust.edu.vn/a", "A"]],
            "extra": 1,
        }
    )
    assert isinstance(record, dict)
    assert record["out-links"] == [("https://hust.edu.vn/a", "A")]
    assert record["extra"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"url": 1},
        {"url": "u", "format": "html", "title": "", "content": ""},
        {"url": "u", "format": "html", "title": "", "content": "", "out-links": [["only-url"]]},
        {"url": "u", "format": "html", "title": "", "content": "", "out-links": "x"},
        {"url": "", "format": "html", "title": "", "content": "", "out-links": []},
    ],
)
def test_validate_record_rejects(bad: object) -> None:
    assert isinstance(validate_record(bad), str)
