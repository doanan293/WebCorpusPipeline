from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from data_pipeline.infrastructure.corpus import kaggle_cli
from data_pipeline.infrastructure.corpus.kaggle_cli import KaggleCli, KaggleError


def test_kaggle_stderr_in_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        stderr = "403 Forbidden: not your dataset"
        raise subprocess.CalledProcessError(1, ["kaggle"], "", stderr)

    monkeypatch.setattr(kaggle_cli.subprocess, "run", failing)

    with pytest.raises(KaggleError, match="403 Forbidden: not your dataset"):
        KaggleCli().create(tmp_path)


def test_list_files_reads_the_csv_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    listing = "Next Page Token = x\nname,size,creationDate\na.jsonl,3,2026\nmanifest.json,1,2026\n"

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, listing, "")

    monkeypatch.setattr(kaggle_cli.subprocess, "run", run)

    assert KaggleCli().list_files("me/corpus/3") == ["a.jsonl", "manifest.json"]
