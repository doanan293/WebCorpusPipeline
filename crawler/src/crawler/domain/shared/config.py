from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    """Well-known locations inside the ``crawler`` project directory."""

    root: Path

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

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
    def data_root(self) -> Path:
        return self.root / "data"

    @property
    def corpus_pulled(self) -> Path:
        return self.root / "data/corpus/pulled"


@dataclass(frozen=True)
class Settings:
    project_root: Path
    concurrency: int = 64
    per_host: int = 2
    host_delay_seconds: float = 0.25
    max_file_bytes: int = 104857600
    timeout_seconds: float = 60
    retries: int = 3
    max_redirects: int = 20
    chunk_bytes: int = 262144
    checkpoint_bytes: int = 1048576
    local_workers: int = 2
    cloud_workers: int = 1
    cloud_poll_seconds: float = 5
    cloud_timeout_seconds: float = 1800
    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_database: str = "information_retrieval"
