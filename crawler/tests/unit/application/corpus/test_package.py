from __future__ import annotations

import json
from pathlib import Path

from crawler.application.corpus.package import package_corpus, restore_data, verify_corpus
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
    db.load(
        "other",
        [{"url": "https://h/f.pdf", "format": "pdf", "origin": "https://h/a", "content": "t"}],
    )
    db.load(
        "picture",
        [{"url": "https://h/i.jpg", "origin": "https://h/a", "format": "jpg", "b64": "AAAA"}],
    )
    db.load(
        "graph",
        [
            {"src": "https://h/a", "dest": "https://h/b", "intro": "B"},
            {"src": "https://h/a", "dest": "https://h/f.pdf", "intro": "F"},
            {"src": "https://h/b", "dest": "https://h/a", "intro": "A"},
        ],
    )
    return db


def test_package_writes_four_files_and_manifest(tmp_path: Path) -> None:
    output = package_corpus(_db(), FILES, tmp_path / "corpus")
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["schema_version"] == "2.0"
    assert manifest["counts"] == {
        "html.jsonl": 2,
        "picture.jsonl": 1,
        "other.jsonl": 1,
        "graph.jsonl": 3,
    }
    assert sorted(manifest["files"]) == [
        "graph.jsonl",
        "html.jsonl",
        "other.jsonl",
        "picture.jsonl",
    ]
    first = json.loads((output / "html.jsonl").read_text().splitlines()[0])
    assert first["url"] == "https://h/a"
    assert verify_corpus(output, FILES)["valid"]


def test_verify_detects_tampered_file(tmp_path: Path) -> None:
    output = package_corpus(_db(), FILES, tmp_path / "corpus")
    (output / "other.jsonl").write_text('{"url": "x"}\n')
    report = verify_corpus(output, FILES)
    assert not report["valid"]
    assert "checksum mismatch other.jsonl" in report["errors"]


def _data(root: Path) -> Path:
    (root / "state").mkdir(parents=True)
    (root / "state" / "mediator.sqlite3").write_bytes(b"queue")
    (root / "submission").mkdir()
    (root / "submission" / "check.py").write_text("print(1)\n")
    (root / "submission" / "db-20261025.archive.gz").write_bytes(b"the mongo dump again")
    (root / "corpus").mkdir()  # the package itself is never packed
    return root


def test_package_packs_the_data_folders_beside_the_collections(tmp_path: Path) -> None:
    output = package_corpus(_db(), FILES, tmp_path / "out", data_root=_data(tmp_path / "data"))
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["archives"] == {
        "data-state.tar.gz.bin": "state",
        "data-submission.tar.gz.bin": "submission",
    }
    assert set(manifest["archives"]) <= set(manifest["files"])
    assert verify_corpus(output, FILES)["valid"]


def test_restore_data_unpacks_missing_folders_and_keeps_the_others(tmp_path: Path) -> None:
    output = package_corpus(_db(), FILES, tmp_path / "out", data_root=_data(tmp_path / "data"))
    target = tmp_path / "fresh"
    (target / "state").mkdir(parents=True)
    (target / "state" / "mediator.sqlite3").write_bytes(b"newer queue")

    assert restore_data(output, target, FILES) == {"state": "kept", "submission": "restored"}
    assert (target / "state" / "mediator.sqlite3").read_bytes() == b"newer queue"
    assert (target / "submission" / "check.py").read_text() == "print(1)\n"
    assert not (target / "submission" / "db-20261025.archive.gz").exists()
