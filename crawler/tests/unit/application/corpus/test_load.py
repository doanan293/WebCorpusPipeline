from __future__ import annotations

from pathlib import Path

import pytest

from crawler.application.corpus.load import load_corpus
from crawler.application.corpus.package import package_corpus
from crawler.infra.corpus.files.corpus_files import LocalCorpusFiles
from tests.fakes.corpus import FakeCorpusDatabase

FILES = LocalCorpusFiles()


def test_load_corpus_fills_every_collection(tmp_path: Path) -> None:
    source = FakeCorpusDatabase()
    source.load(
        "html",
        [{"url": "https://h/a", "format": "html", "title": "A", "content": "a", "intro": ""}],
    )
    source.load("graph", [{"src": "https://h/a", "dest": "https://h/b", "text": "B"}])
    corpus = package_corpus(source, FILES, tmp_path / "corpus")
    target = FakeCorpusDatabase()
    assert load_corpus(corpus, target, FILES) == {"html": 1, "other": 0, "graph": 1}
    assert target.indexed
    assert list(target.collections["html"]) == ["https://h/a"]


def test_load_refuses_an_invalid_corpus(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    with pytest.raises(ValueError, match="invalid corpus"):
        load_corpus(corpus, FakeCorpusDatabase(), FILES)
