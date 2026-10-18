from __future__ import annotations

from data_pipeline.domain.aggregator.links import candidate_urls, in_scope


def test_scope_is_exact_host() -> None:
    hosts = frozenset({"soict.hust.edu.vn"})
    assert in_scope("soict.hust.edu.vn", hosts)
    assert not in_scope("hust.edu.vn", hosts)
    assert not in_scope("www.soict.hust.edu.vn", hosts)
    assert in_scope("127.0.0.1", frozenset({"127.0.0.1"}))


def test_candidates_keep_pages_and_documents_of_scope_hosts() -> None:
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
        allowed_hosts=frozenset({"hust.edu.vn"}),
    ) == ["https://hust.edu.vn/f.pdf", "https://hust.edu.vn/b"]


def test_non_default_port_needs_allowed_port() -> None:
    links = [("http://127.0.0.1:5000/a", "A")]

    def candidates(ports: frozenset[int]) -> list[str]:
        return candidate_urls(
            links,
            source_url="http://127.0.0.1:5000/",
            allowed_hosts=frozenset({"127.0.0.1"}),
            allowed_ports=ports,
        )

    assert candidates(frozenset()) == []
    assert candidates(frozenset({5000})) == ["http://127.0.0.1:5000/a"]
