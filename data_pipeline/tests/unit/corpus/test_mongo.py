from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pymongo.results import DeleteResult

from data_pipeline.corpus import mongo
from data_pipeline.corpus.mongo import _page_value, _record_id, prune_snapshots
from data_pipeline.corpus.query import query_links
from data_pipeline.shared.domain.config import ProjectPaths
from data_pipeline.shared.infrastructure.settings import load_settings
from tests.unit.corpus.test_kaggle import make_corpus


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$ne" in expected:
            if doc.get(key) == expected["$ne"]:
                return False
        elif doc.get(key) != expected:
            return False
    return True


class FakeCollection:
    """Just enough of a pymongo Collection for the corpus commands."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.writes = 0

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next((d for d in self.docs.values() if _matches(d, query)), None)

    def find(
        self, query: dict[str, Any], projection: object = None, *, limit: int = 0
    ) -> list[dict[str, Any]]:
        found = sorted(
            (d for d in self.docs.values() if _matches(d, query)), key=lambda d: str(d["_id"])
        )
        return found[:limit] if limit else found

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], *, upsert: bool) -> None:
        self.docs[doc["_id"]] = doc

    def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        doc = self.find_one(query)
        if doc is not None:
            doc.update(update["$set"])

    def create_index(self, keys: object) -> None:
        return None

    def bulk_write(self, operations: list[Any], *, ordered: bool) -> None:
        for operation in operations:
            doc = operation._doc
            self.docs[doc["_id"]] = doc
            self.writes += 1

    def delete_many(self, query: dict[str, Any]) -> DeleteResult:
        gone = [key for key, doc in self.docs.items() if _matches(doc, query)]
        for key in gone:
            del self.docs[key]
        return DeleteResult({"n": len(gone)}, acknowledged=True)


class FakeDatabase(dict[str, FakeCollection]):
    def __missing__(self, name: str) -> FakeCollection:
        self[name] = FakeCollection()
        return self[name]

    def __getattr__(self, name: str) -> FakeCollection:
        return self[name]


def link_corpus(folder: Path, edges: list[dict[str, str]]) -> Path:
    corpus = make_corpus(folder)
    (corpus / "links.jsonl").write_text("".join(json.dumps(edge) + "\n" for edge in edges))
    manifest = json.loads((corpus / "manifest.json").read_text())
    body = (corpus / "links.jsonl").read_bytes()
    manifest["files"]["links.jsonl"] = {
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
    }
    manifest["snapshot_id"] = hashlib.sha256(
        json.dumps(manifest["files"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    return corpus


EDGES = [
    {
        "source_url": f"https://a.test/{n}",
        "target_url": "https://a.test/hub",
        "relation": "hyperlink",
    }
    for n in range(5)
]


def imported(tmp_path: Path) -> tuple[FakeDatabase, str]:
    db = FakeDatabase()
    corpus = link_corpus(tmp_path / "corpus", EDGES)
    result = mongo.import_corpus(corpus, db)
    return db, str(result["snapshot_id"])


def test_query_refuses_unready_snapshot(tmp_path: Path) -> None:
    db, snapshot = imported(tmp_path)
    db.datasets.update_one({"_id": snapshot}, {"$set": {"status": "importing"}})

    with pytest.raises(ValueError, match="not ready"):
        query_links(db, snapshot, "https://a.test/hub", direction="incoming", limit=10)


def test_query_normalises_url_and_limits(tmp_path: Path) -> None:
    db, snapshot = imported(tmp_path)

    rows = query_links(db, snapshot, " https://a.test/hub#top ", direction="incoming", limit=3)

    assert len(rows) == 3
    assert {row["target_url"] for row in rows} == {"https://a.test/hub"}


def test_import_force_reimports_ready_snapshot(tmp_path: Path) -> None:
    db, _ = imported(tmp_path)
    corpus = tmp_path / "corpus"
    writes = db.links.writes

    assert mongo.import_corpus(corpus, db)["status"] == "already_ready"
    assert db.links.writes == writes
    assert mongo.import_corpus(corpus, db, force=True)["status"] == "ready"
    assert db.links.writes == writes + len(EDGES)


def test_prune_keeps_only_given_snapshot(tmp_path: Path) -> None:
    db, keep = imported(tmp_path)
    old = link_corpus(tmp_path / "old", EDGES[:2])
    old_id = str(mongo.import_corpus(old, db)["snapshot_id"])

    removed = prune_snapshots(db, keep)

    assert removed["links"] == 2
    assert removed["datasets"] == 1
    assert {doc["snapshot_id"] for doc in db.links.docs.values()} == {keep}
    assert db.datasets.find_one({"_id": old_id}) is None


def test_mongo_uri_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_URI", "mongodb://db.example:27017")

    assert load_settings(ProjectPaths(tmp_path)).mongo_uri == "mongodb://db.example:27017"


def test_mongo_page_keeps_the_duplicate_owner() -> None:
    page = _page_value(
        {"url": "https://example.test/copy", "duplicate_of": "https://example.test/a"}
    )
    assert page["duplicate_of"] == "https://example.test/a"


def test_mongo_page_projection_and_edge_ids_are_bounded_and_distinct() -> None:
    page = _page_value(
        {
            "url": "https://example.test/page",
            "text": "plain text",
            "content_html": "x" * 20_000_000,
            "content_blocks": ["layout"],
        }
    )
    assert page == {"url": "https://example.test/page", "text": "plain text"}
    base = {"source_url": "a", "target_url": "b", "anchor_text": "x"}
    assert _record_id({**base, "relation": "hyperlink"}, "links") != _record_id(
        {**base, "relation": "image"}, "links"
    )
