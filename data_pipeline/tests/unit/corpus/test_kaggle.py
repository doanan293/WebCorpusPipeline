from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from data_pipeline.corpus import kaggle
from data_pipeline.corpus.package import verify_corpus

CONFIG = {
    "owner": "me",
    "slug": "corpus",
    "title": "Corpus",
    "subtitle": "A HUST corpus for retrieval",
    "description": "Pages and documents",
    "license_name": "CC0-1.0",
}


def make_corpus(folder: Path) -> Path:
    """Write a valid one-file corpus package."""
    folder.mkdir(parents=True)
    body = b'{"url": "https://a.test/"}\n'
    (folder / "articles.jsonl").write_bytes(body)
    files = {"articles.jsonl": {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}}
    snapshot = hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest: dict[str, object] = {"schema_version": "1.0", "snapshot_id": snapshot, "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest))
    return folder


def write_config(tmp_path: Path, **overrides: object) -> Path:
    path = tmp_path / "kaggle.json"
    values = {**CONFIG, **overrides}
    path.write_text(json.dumps({k: v for k, v in values.items() if v is not None}))
    return path


def test_publish_uploads_only_manifest_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = make_corpus(tmp_path / "corpus")
    uploaded: list[list[str]] = []

    def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        uploaded.append(sorted(p.name for p in Path(args[args.index("-p") + 1]).iterdir()))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(kaggle, "_run", run)
    (corpus / ".env").write_text("SECRET=1")

    with pytest.raises(ValueError, match=r"\.env"):
        kaggle.publish_corpus(corpus, write_config(tmp_path))
    (corpus / ".env").unlink()
    kaggle.publish_corpus(corpus, write_config(tmp_path))

    assert uploaded == [["articles.jsonl", "dataset-metadata.json", "manifest.json"]]


def test_verify_rejects_unlisted_file(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")
    (corpus / "notes.txt").write_text("x")

    report = verify_corpus(corpus)

    assert not report["valid"]
    assert "unlisted file notes.txt" in report["errors"]


def test_pull_downloads_pinned_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_corpus(tmp_path / "remote")
    snapshot = json.loads((source / "manifest.json").read_text())["snapshot_id"]
    lock = tmp_path / "lock.json"
    lock.write_text(
        json.dumps({"owner": "me", "slug": "corpus", "version": 3, "snapshot_id": snapshot})
    )
    refs: list[str] = []

    def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        refs.append(args[3])
        target = Path(args[args.index("-p") + 1])
        for item in source.iterdir():
            (target / item.name).write_bytes(item.read_bytes())
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(kaggle, "_run", run)

    pulled = kaggle.pull_corpus(lock, tmp_path / "pulled")

    assert refs == ["me/corpus/3"]
    assert verify_corpus(pulled)["valid"]


def test_missing_metadata_key_is_a_clear_error(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path / "corpus")

    with pytest.raises(ValueError, match="Kaggle metadata missing: license_name"):
        kaggle.publish_corpus(corpus, write_config(tmp_path, license_name=None))


def test_kaggle_stderr_in_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        stderr = "403 Forbidden: not your dataset"
        raise subprocess.CalledProcessError(1, ["kaggle"], "", stderr)

    monkeypatch.setattr(kaggle.subprocess, "run", failing)

    with pytest.raises(RuntimeError, match="403 Forbidden: not your dataset"):
        kaggle.publish_corpus(make_corpus(tmp_path / "corpus"), write_config(tmp_path))
