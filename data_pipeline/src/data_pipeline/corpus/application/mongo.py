from __future__ import annotations

from pathlib import Path
from typing import Any

from data_pipeline.corpus.application.package import verify_corpus
from data_pipeline.corpus.application.ports import CorpusDatabase, CorpusFiles
from data_pipeline.corpus.domain.manifest import MANIFEST
from data_pipeline.corpus.domain.records import (
    COLLECTION_FILES,
    SNAPSHOT_COLLECTIONS,
    snapshot_record,
)


def import_corpus(
    path: Path, db: CorpusDatabase, files: CorpusFiles, *, force: bool = False
) -> dict[str, Any]:
    """Import a verified corpus into ``db``; a ready snapshot is skipped unless ``force``."""
    report = verify_corpus(path, files)
    if not report["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(report["errors"]))
    snapshot = files.read_json(path / MANIFEST)
    sid = snapshot["snapshot_id"]
    dataset = db.dataset(sid)
    if dataset and dataset.get("status") == "ready" and not force:
        return {"snapshot_id": sid, "status": "already_ready"}
    db.put_dataset({"_id": sid, "snapshot_id": sid, "status": "importing", "manifest": snapshot})
    try:
        db.ensure_indexes()
        for name, collection in COLLECTION_FILES:
            file = path / name
            if files.exists(file):
                db.replace_many(
                    collection,
                    (snapshot_record(row, collection, sid) for row in files.read_jsonl(file)),
                )
        db.set_dataset_fields(sid, {"status": "ready", "error": None})
    except Exception as exc:
        db.set_dataset_fields(sid, {"status": "failed", "error": str(exc)[:1000]})
        raise
    return {"snapshot_id": sid, "status": "ready"}


def prune_snapshots(db: CorpusDatabase, keep: str) -> dict[str, int]:
    """Delete every snapshot but ``keep`` (which must be ready); return deletions per collection."""
    dataset = db.dataset(keep)
    if not dataset or dataset.get("status") != "ready":
        msg = f"snapshot {keep} is not ready in MongoDB; refusing to prune the others"
        raise ValueError(msg)
    removed = {name: db.delete_other_snapshots(name, keep) for name in SNAPSHOT_COLLECTIONS}
    removed["datasets"] = db.delete_other_snapshots("datasets", keep)
    return removed
