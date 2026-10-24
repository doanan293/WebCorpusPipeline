from __future__ import annotations

from pathlib import Path

from crawler.application.corpus.package import verify_corpus
from crawler.application.corpus.ports import CorpusDatabase, CorpusFiles
from crawler.domain.corpus.records import CORPUS_COLLECTIONS, collection_file


def load_corpus(path: Path, db: CorpusDatabase, files: CorpusFiles) -> dict[str, int]:
    """Load a verified corpus package into Mongo; return the rows loaded per collection."""
    report = verify_corpus(path, files)
    if not report["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(report["errors"]))
    db.ensure_indexes()
    loaded: dict[str, int] = {}
    for name in CORPUS_COLLECTIONS:
        source = path / collection_file(name)
        loaded[name] = db.load(name, files.read_jsonl(source)) if files.exists(source) else 0
    return loaded
