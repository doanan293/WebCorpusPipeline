from __future__ import annotations

from pathlib import Path
from typing import Any

from data_pipeline.corpus.application.package import verify_corpus
from data_pipeline.corpus.application.ports import CorpusFiles, DatasetPublisher
from data_pipeline.corpus.domain.kaggle_metadata import dataset_metadata, dataset_ref
from data_pipeline.corpus.domain.manifest import MANIFEST
from data_pipeline.shared.domain.errors import ExternalFailure


def publish_corpus(
    corpus: Path,
    config_path: Path,
    publisher: DatasetPublisher,
    files: CorpusFiles,
    *,
    update: bool = False,
    message: str | None = None,
) -> dict[str, Any]:
    valid = verify_corpus(corpus, files)
    if not valid["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(valid["errors"]))
    config = files.read_json(config_path)
    with files.temporary("ir-kaggle-") as staging:
        # Only the files the manifest lists (verified above) are published, never strays.
        manifest = files.read_json(corpus / MANIFEST)
        for name in [*manifest["files"], MANIFEST]:
            files.copy(corpus / name, staging / name)
        files.write_json(staging / "dataset-metadata.json", dataset_metadata(config))
        if update:
            if not message:
                msg = "--message is required for update"
                raise ValueError(msg)
            publisher.version(staging, message)
            operation = "version"
        else:
            publisher.create(staging)
            operation = "create"
    return {
        "operation": operation,
        "dataset": dataset_ref(config),
        "snapshot_id": valid["snapshot_id"],
    }


def pull_corpus(
    lock_path: Path, destination: Path, publisher: DatasetPublisher, files: CorpusFiles
) -> Path:
    lock = files.read_json(lock_path)
    owner, slug, version = lock.get("owner"), lock.get("slug"), lock.get("version")
    if not owner or not slug or not version:
        msg = "corpus lock needs owner, slug and version"
        raise ValueError(msg)
    destination = destination.resolve()
    with files.temporary("ir-kaggle-pull-") as staging:
        dataset = f"{owner}/{slug}/{version}"
        try:
            publisher.download(dataset, staging)
        except ExternalFailure:
            # Kaggle can expose individual files while its bulk archive endpoint
            # still returns 404 for large datasets. Download each file instead.
            for name in publisher.list_files(dataset):
                publisher.download_file(dataset, name, staging)
        # Kaggle wraps a multi-file dataset in an archive; locate the manifest safely.
        manifests = files.find(staging, MANIFEST)
        if len(manifests) != 1:
            msg = "download must contain exactly one manifest.json"
            raise ValueError(msg)
        corpus = manifests[0].parent
        report = verify_corpus(corpus, files)
        if not report["valid"] or report["snapshot_id"] != lock.get("snapshot_id"):
            msg = "downloaded corpus does not match pinned lock"
            raise ValueError(msg)
        files.install_copy(corpus, destination)
    return destination
