from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Well-known locations inside the ``data_pipeline`` project directory."""

    root: Path

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    @property
    def crawler_config(self) -> Path:
        return self.root / "config/crawler.yaml"

    @property
    def pipeline_config(self) -> Path:
        return self.root / "config/pipeline.yaml"

    @property
    def kaggle_config(self) -> Path:
        return self.root / "config/kaggle-dataset.json"

    @property
    def corpus_lock(self) -> Path:
        return self.root / "config/corpus.lock.json"

    @property
    def domains_all(self) -> Path:
        return self.root / "config/domains/all.txt"

    @property
    def domains_active(self) -> Path:
        return self.root / "config/domains/active.txt"

    @property
    def domains_status(self) -> Path:
        return self.root / "config/domains/status.jsonl"

    @property
    def crawl_final(self) -> Path:
        return self.root / "data/crawl/final"

    @property
    def enrichment(self) -> Path:
        return self.root / "data/enrichment"

    @property
    def corpus_pulled(self) -> Path:
        return self.root / "data/corpus/pulled"


def project_paths(root: Path | None = None) -> ProjectPaths:
    """Return the paths of ``root``, defaulting to the directory holding ``pyproject.toml``."""
    return ProjectPaths(root if root is not None else Path(__file__).resolve().parents[3])
