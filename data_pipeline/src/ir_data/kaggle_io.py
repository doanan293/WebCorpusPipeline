from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .package import verify_corpus


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, env=None)


def _metadata(config: dict, folder: Path) -> Path:
    required = ("owner", "slug", "title", "subtitle", "description", "license_name")
    missing = [key for key in required if not str(config.get(key, ".")).strip()]
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
        raise ValueError("Kaggle did not return a file listing") from exc
    rows = csv.DictReader(io.StringIO("\n".join(lines[header:])))
    names = [str(row["name"]).strip() for row in rows if row.get("name")]
    if not names:
        raise ValueError("Kaggle dataset file listing is empty")
    return names


def publish_corpus(
    corpus: Path, config_path: Path, update: bool = False, message: str | None = None
) -> dict:
    valid = verify_corpus(corpus)
    if not valid["valid"]:
        raise ValueError("invalid corpus: " + "; ".join(valid["errors"]))
    config = json.loads(config_path.read_text())
    with tempfile.TemporaryDirectory(prefix="ir-kaggle-") as temp:
        staging = Path(temp)
        for item in corpus.iterdir():
            if item.is_file():
                shutil.copy2(item, staging / item.name)
        _metadata(config, staging)
        if update:
            if not message:
                raise ValueError("--message is required for update")
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
    owner = lock.get("owner")
    slug = lock.get("slug")
    if not owner or not slug:
        raise ValueError("corpus lock needs owner and slug")
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ir-kaggle-pull-") as temp:
        staging = Path(temp)
        dataset = f"{owner}/{slug}"
        try:
            _run(["kaggle", "datasets", "download", dataset, "-p", str(staging), "--unzip"])
        except subprocess.CalledProcessError:
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
            raise ValueError("download must contain exactly one manifest.json")
        corpus = manifests[0].parent
        report = verify_corpus(corpus)
        if not report["valid"] or report["snapshot_id"] != lock.get("snapshot_id"):
            raise ValueError("downloaded corpus does not match pinned lock")
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(corpus, destination)
    return destination
