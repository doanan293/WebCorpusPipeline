from __future__ import annotations

from pathlib import Path
from typing import Any

from data_pipeline.application.corpus.ports import CorpusDatabase, CorpusFiles
from data_pipeline.domain.corpus.manifest import (
    MANIFEST,
    Fingerprint,
    build_manifest,
    verify_manifest,
)
from data_pipeline.domain.corpus.records import CORPUS_COLLECTIONS, collection_file


def package_corpus(db: CorpusDatabase, files: CorpusFiles, output: Path) -> Path:
    """Export the Mongo collections to a corpus package next to ``output`` and swap it in."""
    output = output.resolve()
    with files.staging(output) as staging:
        listed: dict[str, Any] = {}
        counts: dict[str, int] = {}
        for name in CORPUS_COLLECTIONS:
            path = staging / collection_file(name)
            counts[path.name] = files.write_jsonl(path, db.iter_collection(name))
            sha256, size = files.fingerprint(path)
            listed[path.name] = {"sha256": sha256, "size": size}
        files.write_json(staging / MANIFEST, build_manifest(listed, counts))
        files.replace(staging, output)
    return output


def verify_corpus(path: Path, files: CorpusFiles) -> dict[str, Any]:
    manifest = path / MANIFEST
    if not files.exists(manifest):
        return {"valid": False, "errors": ["manifest.json missing"]}
    try:
        value = files.read_json(manifest)
    except Exception as exc:  # noqa: BLE001 -- any unreadable manifest is reported as invalid, not raised
        return {"valid": False, "errors": [f"invalid manifest: {exc}"]}
    listed = value.get("files", {})
    present: dict[str, Fingerprint | None] = {
        name: files.fingerprint(path / name) if name in listed else None
        for name in files.names(path)
    }
    errors = verify_manifest(value, present)
    return {"valid": not errors, "snapshot_id": value.get("snapshot_id"), "errors": errors}
