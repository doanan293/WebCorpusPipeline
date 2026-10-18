from __future__ import annotations

from data_pipeline.domain.aggregator.links import candidate_urls, in_scope


def test_in_scope() -> None:
    assert in_scope("hust.edu.vn", frozenset(), ("hust.edu.vn",))
    assert in_scope("soict.hust.edu.vn", frozenset(), ("hust.edu.vn",))
    assert not in_scope("nothust.edu.vn", frozenset(), ("hust.edu.vn",))
    assert in_scope("127.0.0.1", frozenset({"127.0.0.1"}), ())


def test_candidates_keep_hust_pages_and_documents() -> None:
    links = [
        ("https://soict.hust.edu.vn/a", "A"),
        ("https://google.com/", "G"),
        ("https://hust.edu.vn/f.pdf", "PDF"),
        ("https://hust.edu.vn/logo.png", ""),
        ("https://hust.edu.vn/search?q=a", ""),
        ("https://hust.edu.vn/login", "Đăng nhập"),
        ("https://soict.hust.edu.vn/a#top", ""),
        ("mailto:a@hust.edu.vn", ""),
        ("/b", "relative"),
    ]
    assert candidate_urls(
        links,
        source_url="https://hust.edu.vn/",
        allowed_hosts=frozenset(),
        allowed_suffixes=("hust.edu.vn",),
    ) == ["https://soict.hust.edu.vn/a", "https://hust.edu.vn/f.pdf", "https://hust.edu.vn/b"]
