from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from data_pipeline.core.paths import ProjectPaths


@dataclass(frozen=True)
class Settings:
    project_root: Path
    work_dir: Path = Path("data/enrichment")
    source: Path = Path("data/crawl/final")
    concurrency: int = 64
    per_host: int = 2
    host_delay_seconds: float = 0.25
    max_file_bytes: int = 104857600
    max_storage_bytes: int = 107374182400
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
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def source_path(self) -> Path:
        return self.source if self.source.is_absolute() else self.project_root / self.source

    @property
    def work_path(self) -> Path:
        return self.work_dir if self.work_dir.is_absolute() else self.project_root / self.work_dir


def _numbered_key(name: str) -> tuple[int, str]:
    m = re.search(r"(\d+)$", name)
    return (int(m.group(1)) if m else 0, name)


def load_api_keys(env_file: Path) -> list[tuple[str, str]]:
    values = {**dotenv_values(env_file), **os.environ}
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in sorted(
        (k for k in values if re.fullmatch(r"LLAMAPARSE_KEY\d+", k)), key=_numbered_key
    ):
        value = str(values[name] or "").strip()
        if value and value not in seen:
            result.append((name, value))
            seen.add(value)
    return result


def _positive(name: str, value: Any, *, integer: bool = True) -> int | float:
    result = int(value) if integer else float(value)
    if result <= 0:
        msg = f"{name} must be positive"
        raise ValueError(msg)
    return result


def load_settings(paths: ProjectPaths, overrides: Mapping[str, Any] | None = None) -> Settings:
    config_path = paths.pipeline_config
    loaded = yaml.safe_load(config_path.read_text()) if config_path.exists() else None
    config: dict[str, Any] = loaded or {}
    d = {**config.get("download", {}), **config.get("parser", {})}
    db = config.get("database", {})
    values: dict[str, Any] = {
        "work_dir": "data/enrichment",
        "source": "data/crawl/final",
        "concurrency": d.get("concurrency", 64),
        "per_host": d.get("per_host", 2),
        "host_delay_seconds": d.get("host_delay_seconds", 0.25),
        "max_file_bytes": d.get("max_file_bytes", 104857600),
        "max_storage_bytes": d.get("max_storage_bytes", 107374182400),
        "timeout_seconds": d.get("request_timeout_seconds", 60),
        "retries": d.get("retries", 3),
        "max_redirects": d.get("max_redirects", 20),
        "chunk_bytes": d.get("chunk_bytes", 262144),
        "checkpoint_bytes": d.get("checkpoint_bytes", 1048576),
        "local_workers": d.get("local_workers", 2),
        "cloud_workers": d.get("cloud_workers", 1),
        "cloud_poll_seconds": d.get("cloud_poll_seconds", 5),
        "cloud_timeout_seconds": d.get("cloud_timeout_seconds", 1800),
        "mongo_uri": db.get("mongo_uri", "mongodb://127.0.0.1:27017"),
        "mongo_database": db.get("mongo_database", "information_retrieval"),
    }
    values.update(overrides or {})
    if values["per_host"] > values["concurrency"]:
        msg = "per_host cannot exceed concurrency"
        raise ValueError(msg)
    for key in (
        "concurrency",
        "per_host",
        "max_file_bytes",
        "max_storage_bytes",
        "retries",
        "max_redirects",
        "chunk_bytes",
        "checkpoint_bytes",
        "local_workers",
        "cloud_workers",
    ):
        values[key] = _positive(key, values[key])
    for key in (
        "host_delay_seconds",
        "timeout_seconds",
        "cloud_poll_seconds",
        "cloud_timeout_seconds",
    ):
        values[key] = _positive(key, values[key], integer=False)
    values["project_root"] = paths.root.resolve()
    values["work_dir"] = Path(values["work_dir"])
    values["source"] = Path(values["source"])
    return Settings(**values)
