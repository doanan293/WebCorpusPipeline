from __future__ import annotations

import json
from pathlib import Path

from crawler.application.corpus.package import package_corpus, verify_corpus
from crawler.infra.corpus.files.corpus_files import LocalCorpusFiles
from tests.fakes.corpus import FakeCorpusDatabase

FILES = LocalCorpusFiles()


def _db() -> FakeCorpusDatabase:
    db = FakeCorpusDatabase()
    db.load(
        "html",
        [
            {"url": "https://h/a", "format": "html", "title": "A", "content": "a", "intro": ""},
            {"url": "https://h/b", "format": "html", "title": "B", "content": "b", "intro": ""},
        ],
    )
    db.load("other", [{"url": "https://h/f.pdf", "format": "pdf", "content": "t"}])
    db.load(
        "graph",
        [
            {"src": "https://h/a", "dest": "https://h/b", "text": "B"},
            {"src": "https://h/a", "dest": "https://h/f.pdf", "text": "F"},
            {"src": "https://h/b", "dest": "https://h/a", "text": "A"},
        ],
    )
    return db


def test_package_writes_three_files_and_manifest(tmp_path: Path) -> None:
    output = package_corpus(_db(), FILES, tmp_path / "corpus")
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["counts"] == {"html.jsonl": 2, "other.jsonl": 1, "graph.jsonl": 3}
    assert sorted(manifest["files"]) == ["graph.jsonl", "html.jsonl", "other.jsonl"]
    first = json.loads((output / "html.jsonl").read_text().splitlines()[0])
    assert first["url"] == "https://h/a"
    assert verify_corpus(output, FILES)["valid"]


def test_verify_detects_tampered_file(tmp_path: Path) -> None:
    output = package_corpus(_db(), FILES, tmp_path / "corpus")
    (output / "other.jsonl").write_text('{"url": "x"}\n')
    report = verify_corpus(output, FILES)
    assert not report["valid"]
    assert "checksum mismatch other.jsonl" in report["errors"]
