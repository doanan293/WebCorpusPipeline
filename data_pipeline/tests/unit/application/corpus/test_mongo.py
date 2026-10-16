from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from data_pipeline.application.corpus.mongo import import_corpus, prune_snapshots
from data_pipeline.application.corpus.query import query_links
from data_pipeline.domain.shared.config import ProjectPaths
from data_pipeline.infrastructure.corpus.files.corpus_files import LocalCorpusFiles
from data_pipeline.infrastructure.shared.settings import load_settings
from tests.fakes.corpus import FakeCorpusDatabase
from tests.unit.application.corpus.test_kaggle import make_corpus

FILES = LocalCorpusFiles()


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


def imported(tmp_path: Path) -> tuple[FakeCorpusDatabase, str]:
    db = FakeCorpusDatabase()
    corpus = link_corpus(tmp_path / "corpus", EDGES)
    result = import_corpus(corpus, db, FILES)
    return db, str(result["snapshot_id"])


def test_import_stores_pages_and_links_and_marks_the_snapshot_ready(tmp_path: Path) -> None:
    db, snapshot = imported(tmp_path)

    assert db.indexed
    assert db.dataset(snapshot) is not None
    assert (db.dataset(snapshot) or {})["status"] == "ready"
    assert len(db.collections["links"]) == len(EDGES)
    assert [page["url"] for page in db.collections["pages"].values()] == ["https://a.test/"]


def test_import_rejects_an_invalid_corpus(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")
    (corpus / "notes.txt").write_text("x")

    with pytest.raises(ValueError, match=r"invalid corpus: unlisted file notes\.txt"):
        import_corpus(corpus, FakeCorpusDatabase(), FILES)


def test_query_refuses_unready_snapshot(tmp_path: Path) -> None:
    db, snapshot = imported(tmp_path)
    db.set_dataset_fields(snapshot, {"status": "importing"})

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
    writes = db.writes["links"]

    assert import_corpus(corpus, db, FILES)["status"] == "already_ready"
    assert db.writes["links"] == writes
    assert import_corpus(corpus, db, FILES, force=True)["status"] == "ready"
    assert db.writes["links"] == writes + len(EDGES)


def test_prune_keeps_only_given_snapshot(tmp_path: Path) -> None:
    db, keep = imported(tmp_path)
    old = link_corpus(tmp_path / "old", EDGES[:2])
    old_id = str(import_corpus(old, db, FILES)["snapshot_id"])

    removed = prune_snapshots(db, keep)

    assert removed["links"] == 2
    assert removed["datasets"] == 1
    assert {doc["snapshot_id"] for doc in db.collections["links"].values()} == {keep}
    assert db.dataset(old_id) is None


def test_prune_refuses_a_snapshot_that_is_not_ready(tmp_path: Path) -> None:
    db, keep = imported(tmp_path)
    db.set_dataset_fields(keep, {"status": "failed"})

    with pytest.raises(ValueError, match="refusing to prune"):
        prune_snapshots(db, keep)


def test_mongo_uri_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MONGO_URI", "mongodb://db.example:27017")

    assert load_settings(ProjectPaths(tmp_path)).mongo_uri == "mongodb://db.example:27017"
