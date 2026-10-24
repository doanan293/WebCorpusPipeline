from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from crawler.application.corpus.kaggle import publish_corpus, pull_corpus
from crawler.application.corpus.package import verify_corpus
from crawler.infra.corpus.files.corpus_files import LocalCorpusFiles
from tests.fakes.corpus import FakePublisher

FILES = LocalCorpusFiles()
CONFIG = {
    "owner": "me",
    "slug": "corpus",
    "title": "Corpus",
    "subtitle": "A HUST corpus for retrieval",
    "description": "Pages and documents",
    "license_name": "CC0-1.0",
}


def make_corpus(folder: Path) -> Path:
    """Write a valid one-file corpus package."""
    folder.mkdir(parents=True)
    body = b'{"url": "https://a.test/"}\n'
    (folder / "articles.jsonl").write_bytes(body)
    files = {"articles.jsonl": {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}}
    snapshot = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest: dict[str, object] = {"schema_version": "1.0", "snapshot_id": snapshot, "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def write_config(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "kaggle.json"
    values = {**CONFIG, **overrides}
    path.write_text(json.dumps({k: v for k, v in values.items() if v is not None}))
    return path


def write_lock(tmp_path: Path, source: Path) -> Path:
    snapshot = json.loads((source / "manifest.json").read_text())["snapshot_id"]
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps({"owner": "me", "slug": "corpus", "version": 3, "snapshot_id": snapshot})
    )
    return lock


def test_publish_uploads_only_manifest_files(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")
    publisher = FakePublisher()
    (corpus / ".env").write_text("SECRET=1")

    with pytest.raises(ValueError, match=r"\.env"):
        publish_corpus(corpus, write_config(tmp_path), publisher, FILES)
    (corpus / ".env").unlink()
    result = publish_corpus(corpus, write_config(tmp_path), publisher, FILES)

    assert publisher.uploads == [
        ("create", ["articles.jsonl", "dataset-metadata.json", "manifest.json"])
    ]
    assert result["dataset"] == "me/corpus"


def test_update_needs_a_message_and_publishes_a_version(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")
    publisher = FakePublisher()

    with pytest.raises(ValueError, match="--message"):
        publish_corpus(corpus, write_config(tmp_path), publisher, FILES, update=True)
    result = publish_corpus(
        corpus, write_config(tmp_path), publisher, FILES, update=True, message="v2"
    )

    assert result["operation"] == "version"
    assert [operation for operation, _ in publisher.uploads] == ["version"]


def test_verify_rejects_unlisted_file(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")
    (corpus / "notes.txt").write_text("x")

    report = verify_corpus(corpus, FILES)

    assert not report["valid"]
    assert "unlisted file notes.txt" in report["errors"]


def test_pull_downloads_pinned_version(tmp_path: Path) -> None:
    source = make_corpus(tmp_path / "remote")
    publisher = FakePublisher(source)

    pulled = pull_corpus(write_lock(tmp_path, source), tmp_path / "pulled", publisher, FILES)

    assert publisher.downloads == ["me/corpus/3"]
    assert verify_corpus(pulled, FILES)["valid"]


def test_pull_falls_back_to_single_files_when_the_archive_fails(tmp_path: Path) -> None:
    source = make_corpus(tmp_path / "remote")
    publisher = FakePublisher(source, bulk_fails=True)

    pulled = pull_corpus(write_lock(tmp_path, source), tmp_path / "pulled", publisher, FILES)

    assert verify_corpus(pulled, FILES)["valid"]


def test_missing_metadata_key_is_a_clear_error(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")

    with pytest.raises(ValueError, match="Kaggle metadata missing: license_name"):
        publish_corpus(corpus, write_config(tmp_path, license_name=None), FakePublisher(), FILES)
