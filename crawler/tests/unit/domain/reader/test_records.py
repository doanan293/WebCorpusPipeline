from __future__ import annotations

import hashlib
from typing import Any

from crawler.domain.reader.extract.article import analyze_article
from crawler.domain.reader.extract.fingerprint import change_fingerprint
from crawler.domain.reader.records import (
    content_id_for,
    document_format,
    html_record,
    other_record,
    record_from_article,
)

HTML = (
    b"<html><head><title>Tin 1</title><meta name='description' content='Gioi thieu'></head>"
    b"<body><article><h1>Tin 1</h1><p>Noi dung bai.</p><a href='/b'>Bai B</a>"
    b"<img src='/anh.jpg' alt='Anh'></article></body></html>"
)


def test_html_record_maps_slide_fields() -> None:
    record = html_record("https://hust.edu.vn/a", analyze_article(HTML, "https://hust.edu.vn/a"))
    assert record["url"] == "https://hust.edu.vn/a"
    assert record["format"] == "html"
    assert record["title"] == "Tin 1"
    assert "Noi dung bai." in record["content"]
    assert "intro" not in record  # storage sets intro from the links to the page
    assert ["https://hust.edu.vn/b", "Bai B"] in record["out-links"]
    assert "https://hust.edu.vn/anh.jpg" in record["images"]
    assert record["content_id"] == hashlib.sha256(record["content"].strip().encode()).hexdigest()
    assert {
        "published_at",
        "modified_at",
        "author",
        "language",
        "headings",
        "content_blocks",
    } <= set(record)


def test_record_from_stored_article_uses_observed_refs() -> None:
    article: dict[str, Any] = {
        "title": None,
        "text": " body ",
        "summary": None,
        "observed_refs": [{"url": "https://h/x", "text": "X", "kind": "page", "rel": []}],
        "assets": [
            {"url": "https://h/i.png", "role": "inline_image"},
            {"url": "https://h/f.pdf", "role": "document_attachment"},
        ],
    }
    record = record_from_article("https://h/", article)
    assert record["title"] == ""
    assert "intro" not in record
    assert record["out-links"] == [["https://h/x", "X"]]
    assert record["images"] == ["https://h/i.png"]
    assert record["content_id"] == content_id_for("body")


def test_document_format() -> None:
    assert document_format("https://x/a.DOCX", "") == "docx"
    assert document_format("https://x/dl?id=1", "application/pdf") == "pdf"
    assert document_format("https://x/dl", "application/zip") == "bin"


def test_other_record() -> None:
    record = other_record("https://x/a.pdf", "pdf", "Van ban")
    assert record == {
        "url": "https://x/a.pdf",
        "format": "pdf",
        "title": "",
        "content": "Van ban",
        "out-links": [],
        "content_id": content_id_for("Van ban"),
        "fingerprint": change_fingerprint("Van ban"),
    }


def test_out_links_carry_the_sentence_holding_the_link() -> None:
    page = (
        "<html><head><meta charset='utf-8'><title>T</title></head><body><article><h1>T</h1>"
        "<p>Mọi thông tin xem tại <a href='/tb'>trang thông báo</a> của trường.</p>"
        "</article></body></html>"
    ).encode()
    record = html_record("https://hust.edu.vn/a", analyze_article(page, "https://hust.edu.vn/a"))
    assert [
        "https://hust.edu.vn/tb",
        "Mọi thông tin xem tại trang thông báo của trường.",
    ] in record["out-links"]


def test_stored_article_without_sentences_keeps_its_labels() -> None:
    """An article stored before sentences existed cannot tell links from images: keep them all."""
    article: dict[str, Any] = {
        "text": "body",
        "observed_refs": [
            {"url": "https://h/x", "text": "X", "kind": "page", "rel": []},
            {"url": "https://h/i.png", "text": "", "kind": "file", "rel": []},
        ],
    }
    record = record_from_article("https://h/", article)
    assert record["out-links"] == [["https://h/x", "X"], ["https://h/i.png", ""]]


def test_fresh_article_uses_sentences_and_drops_embedded_resources() -> None:
    article: dict[str, Any] = {
        "text": "body",
        "observed_refs": [
            {
                "url": "https://h/y",
                "text": "Y",
                "sentence": "Xem Y nhé.",
                "kind": "page",
                "rel": [],
            },
            {"url": "https://h/i.png", "text": "", "kind": "file", "rel": []},
        ],
    }
    record = record_from_article("https://h/", article)
    assert record["out-links"] == [["https://h/y", "Xem Y nhé."]]


def test_out_links_are_links_not_embedded_resources() -> None:
    """The slide's out-links are links (thẻ a); images go to ``images``. NukeViet pages embed
    <img src="/index.php?second=cronjobs&p=<random>">: as an out-link, every page made a new URL."""
    page = (
        "<html><head><meta charset='utf-8'><title>T</title>"
        "<link rel='next' href='/tin?page=2'></head><body><article><h1>T</h1>"
        "<p>Xem <a href='/tb'>thông báo</a> mới.</p><img src='/anh.jpg' alt='Anh'>"
        "<img src='/index.php?second=cronjobs&amp;p=x1y2'></article></body></html>"
    ).encode()
    record = html_record("https://hust.edu.vn/a", analyze_article(page, "https://hust.edu.vn/a"))
    assert [url for url, _ in record["out-links"]] == [
        "https://hust.edu.vn/tin?page=2",
        "https://hust.edu.vn/tb",
    ]
    assert "https://hust.edu.vn/anh.jpg" in record["images"]


def test_records_carry_a_change_fingerprint() -> None:
    v1 = other_record("https://h/a.pdf", "pdf", "Lịch thi ngày 12 tháng 5 năm 2026\nPhòng D3")
    v1_dates = other_record("https://h/a.pdf", "pdf", "Lịch thi ngày 14 tháng 6 năm 2026\nPhòng D3")
    v2 = other_record("https://h/a.pdf", "pdf", "Lịch thi\nPhòng D9")
    assert v1["fingerprint"] == v1_dates["fingerprint"]  # only dates moved
    assert v1["fingerprint"] != v2["fingerprint"]
    assert other_record("https://h/b.pdf", "pdf", "  ")["fingerprint"] is None
