from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from pymongo import MongoClient

from scripts import migrate_mongo


def test_html_documents_use_final_urls_and_mark_duplicates(crawl: Path) -> None:
    finals = migrate_mongo.final_urls(crawl)
    docs = {doc["url"]: doc for doc in migrate_mongo.html_documents(crawl, finals, "now")}
    assert set(docs) == {
        "https://h.hust.edu.vn/",
        "https://h.hust.edu.vn/a2",
        "https://h.hust.edu.vn/dup",
    }
    home = docs["https://h.hust.edu.vn/"]
    assert home["content"] == "home" and home["intro"] == "gioi thieu"
    assert home["images"] == ["https://h.hust.edu.vn/anh.jpg"]
    assert docs["https://h.hust.edu.vn/a2"]["content"] == "same"
    assert docs["https://h.hust.edu.vn/dup"]["content"] == ""
    assert docs["https://h.hust.edu.vn/dup"]["duplicate_of"] == "https://h.hust.edu.vn/a2"


def test_other_documents_join_parts(corpus: Path) -> None:
    (doc,) = migrate_mongo.other_documents(corpus, "now")
    assert doc == {
        "url": "https://h.hust.edu.vn/qd.pdf",
        "format": "pdf",
        "content": "trang 1\n\ntrang 3",
        "title": "",
        "content_id": doc["content_id"],
        "updated_at": "now",
    }


def test_graph_edges_skip_images_and_use_final_sources(crawl: Path, corpus: Path) -> None:
    edges = list(migrate_mongo.graph_edges(corpus, migrate_mongo.final_urls(crawl)))
    assert edges == [
        {"src": "https://h.hust.edu.vn/a2", "dest": "https://h.hust.edu.vn/b", "text": "B"},
        {"src": "https://h.hust.edu.vn/", "dest": "https://h.hust.edu.vn/qd.pdf", "text": "QD"},
    ]


@pytest.mark.docker
def test_main_writes_the_three_collections(
    crawl: Path, corpus: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uri = os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017")
    name = f"test_{uuid.uuid4().hex}"
    client: MongoClient[dict[str, object]] = MongoClient(uri, serverSelectionTimeoutMS=3000)
    try:
        args = [
            "--crawl",
            str(crawl),
            "--corpus",
            str(corpus),
            "--mongodb-uri",
            uri,
            "--database",
            name,
        ]
        assert migrate_mongo.main(args) == 0
        assert migrate_mongo.main(args) == 0
        assert capsys.readouterr().out.split()[-3:] == ["html=3", "other=1", "graph=2"]
        db = client[name]
        assert (
            db.html.count_documents({}),
            db.other.count_documents({}),
            db.graph.count_documents({}),
        ) == (3, 1, 2)
    finally:
        client.drop_database(name)
