from __future__ import annotations

from crawler.domain.mediator.kinds import url_kind


def test_url_kind() -> None:
    assert url_kind("https://hust.edu.vn/") == "hub"
    assert url_kind("https://hust.edu.vn") == "hub"
    assert url_kind("https://hust.edu.vn/files/QD.PDF") == "document"
    assert url_kind("https://hust.edu.vn/files/data.csv?v=2") == "document"
    assert url_kind("https://hust.edu.vn/tin-tuc/a") == "page"


def test_listing_pages_are_hubs() -> None:
    assert url_kind("https://hust.edu.vn/tin-tuc") == "hub"
    assert url_kind("https://soict.hust.edu.vn/thong-bao/?page=1") == "hub"
    assert url_kind("https://soict.hust.edu.vn/thong-bao/?page=2") == "page"
    assert url_kind("https://hust.edu.vn/tin-tuc/page/3") == "page"
    assert url_kind("https://hust.edu.vn/tin-tuc/hoi-thao-ai-123.html") == "page"
    assert url_kind("https://hust.edu.vn/tai-lieu.pdf") == "document"
