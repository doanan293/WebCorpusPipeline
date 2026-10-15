from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from data_pipeline.core.errors import ExternalFailure
from data_pipeline.corpus.package import swap_into_place, verify_corpus


class KaggleError(ExternalFailure):
    """A kaggle CLI call failed; the message carries its stderr."""


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 -- fixed argv, no shell
            args, cwd=cwd, check=True, capture_output=True, text=True, env=None
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        msg = f"{' '.join(args[:3])} failed: {detail}"
        raise KaggleError(msg) from exc


def _metadata(config: dict[str, Any], folder: Path) -> Path:
    required = ("owner", "slug", "title", "subtitle", "description", "license_name")
    missing = [key for key in required if not str(config.get(key, "")).strip()]
    if missing:
        raise ValueError("Kaggle metadata missing: " + ", ".join(missing))
    value = {
        "title": config["title"],
        "id": f"{config['owner']}/{config['slug']}",
        "subtitle": config["subtitle"],
        "description": config["description"],
        "licenses": [{"name": config["license_name"]}],
        "keywords": ["information retrieval", "HUST", "Vietnamese", "documents"],
    }
    path = folder / "dataset-metadata.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return path


def _dataset_files(dataset: str) -> list[str]:
    result = _run(["kaggle", "datasets", "files", dataset, "-v", "--page-size", "200"])
    lines = result.stdout.splitlines()
    try:
        header = next(
            index for index, line in enumerate(lines) if line.strip() == "name,size,creationDate"
        )
    except StopIteration as exc:
        msg = "Kaggle did not return a file listing"
        raise ValueError(msg) from exc
    rows = csv.DictReader(io.StringIO("\n".join(lines[header:])))
    names = [str(row["name"]).strip() for row in rows if row.get("name")]
    if not names:
        msg = "Kaggle dataset file listing is empty"
        raise ValueError(msg)
    return names


def publish_corpus(
    corpus: Path, config_path: Path, *, update: bool = False, message: str | None = None
) -> dict[str, Any]:
    valid = verify_corpus(corpus)
    if not valid["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(valid["errors"]))
    config = json.loads(config_path.read_text())
    with tempfile.TemporaryDirectory(prefix="ir-kaggle-") as temp:
        staging = Path(temp)
        # Only the files the manifest lists (verified above) are published, never strays.
        manifest = json.loads((corpus / "manifest.json").read_text())
        for name in [*manifest["files"], "manifest.json"]:
            shutil.copy2(corpus / name, staging / name)
        _metadata(config, staging)
        if update:
            if not message:
                msg = "--message is required for update"
                raise ValueError(msg)
            _run(
                [
                    "kaggle",
                    "datasets",
                    "version",
                    "-p",
                    str(staging),
                    "-m",
                    message,
                    "-t",
                    "-r",
                    "skip",
                ]
            )
            operation = "version"
        else:
            _run(["kaggle", "datasets", "create", "-p", str(staging), "-u", "-t", "-r", "skip"])
            operation = "create"
    return {
        "operation": operation,
        "dataset": f"{config['owner']}/{config['slug']}",
        "snapshot_id": valid["snapshot_id"],
    }


def pull_corpus(lock_path: Path, destination: Path) -> Path:
    lock = json.loads(lock_path.read_text())
    owner, slug, version = lock.get("owner"), lock.get("slug"), lock.get("version")
    if not owner or not slug or not version:
        msg = "corpus lock needs owner, slug and version"
        raise ValueError(msg)
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ir-kaggle-pull-") as temp:
        staging = Path(temp)
        dataset = f"{owner}/{slug}/{version}"
        try:
            _run(["kaggle", "datasets", "download", dataset, "-p", str(staging), "--unzip"])
        except KaggleError:
            # Kaggle can expose individual files while its bulk archive endpoint
            # still returns 404 for large datasets. Download each file instead.
            for name in _dataset_files(dataset):
                _run(
                    [
                        "kaggle",
                        "datasets",
                        "download",
                        dataset,
                        "-f",
                        name,
                        "-p",
                        str(staging),
                        "--unzip",
                    ]
                )
        # Kaggle wraps a multi-file dataset in an archive; locate the manifest safely.
        manifests = list(staging.rglob("manifest.json"))
        if len(manifests) != 1:
            msg = "download must contain exactly one manifest.json"
            raise ValueError(msg)
        corpus = manifests[0].parent
        report = verify_corpus(corpus)
        if not report["valid"] or report["snapshot_id"] != lock.get("snapshot_id"):
            msg = "downloaded corpus does not match pinned lock"
            raise ValueError(msg)
        staged = destination.with_name(f"{destination.name}.tmp-{os.getpid()}")
        shutil.rmtree(staged, ignore_errors=True)
        try:
            shutil.copytree(corpus, staged)
            staged.chmod(0o755)
            swap_into_place(staged, destination)
        finally:
            shutil.rmtree(staged, ignore_errors=True)
    return destination
