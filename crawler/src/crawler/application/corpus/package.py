from __future__ import annotations

from pathlib import Path
from typing import Any

from crawler.application.corpus.ports import CorpusDatabase, CorpusFiles
from crawler.domain.corpus.manifest import (
    MANIFEST,
    Fingerprint,
    build_manifest,
    verify_manifest,
)
from crawler.domain.corpus.records import (
    CORPUS_COLLECTIONS,
    DATA_EXCLUDE,
    DATA_FOLDERS,
    collection_file,
    data_archive,
)


def package_corpus(
    db: CorpusDatabase, files: CorpusFiles, output: Path, *, data_root: Path | None = None
) -> Path:
    """Export the Mongo collections to a corpus package next to ``output`` and swap it in.

    With ``data_root``, its data folders that exist are packed into the package too.
    """
    output = output.resolve()
    with files.staging(output) as staging:
        listed: dict[str, Any] = {}
        counts: dict[str, int] = {}
        for name in CORPUS_COLLECTIONS:
            path = staging / collection_file(name)
            counts[path.name] = files.write_jsonl(path, db.iter_collection(name))
            sha256, size = files.fingerprint(path)
            listed[path.name] = {"sha256": sha256, "size": size}
        archives: dict[str, str] = {}
        for folder in DATA_FOLDERS:
            if data_root is None:
                break
            source = data_root / folder
            if not files.exists(source):
                continue
            path = staging / data_archive(folder)
            files.pack(source, path, DATA_EXCLUDE)
            sha256, size = files.fingerprint(path)
            listed[path.name] = {"sha256": sha256, "size": size}
            archives[path.name] = folder
        files.write_json(staging / MANIFEST, build_manifest(listed, counts, archives))
        files.replace(staging, output)
    return output


def restore_data(corpus: Path, data_root: Path, files: CorpusFiles) -> dict[str, str]:
    """Unpack the package's data folders under ``data_root``: ``restored``, or ``kept`` when the
    folder already holds files, which are never overwritten."""
    archives: dict[str, str] = files.read_json(corpus / MANIFEST).get("archives", {})
    outcome: dict[str, str] = {}
    for name, folder in archives.items():
        destination = data_root / folder
        if files.exists(destination) and files.names(destination):
            outcome[folder] = "kept"
            continue
        files.unpack(corpus / name, destination)
        outcome[folder] = "restored"
    return outcome


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
