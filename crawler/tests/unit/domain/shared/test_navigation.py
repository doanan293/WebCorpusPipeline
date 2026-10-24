from __future__ import annotations

from crawler.domain.shared.navigation import is_later_page, is_listing_page


def test_listing_pages() -> None:
    assert is_listing_page("https://hust.edu.vn/tin-tuc")
    assert is_listing_page("https://hust.edu.vn/vi/news/tin-tuc-su-kien/")
    assert is_listing_page("https://soict.hust.edu.vn/thong-bao/?page=1")
    assert is_listing_page("https://hust.edu.vn/vi/")
    assert not is_listing_page("https://hust.edu.vn/tin-tuc/hoi-thao-ai-123.html")
    assert not is_listing_page("https://hust.edu.vn/gioi-thieu")


def test_later_pages() -> None:
    assert is_later_page("https://soict.hust.edu.vn/thong-bao/?page=2")
    assert is_later_page("https://hust.edu.vn/tin-tuc/page/3")
    assert is_later_page("https://hust.edu.vn/tin-tuc/trang/2")
    assert not is_later_page("https://soict.hust.edu.vn/thong-bao/?page=1")
    assert not is_later_page("https://hust.edu.vn/tin-tuc")
