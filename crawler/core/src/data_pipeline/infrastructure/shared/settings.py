from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from data_pipeline.domain.shared.config import ProjectPaths, Settings


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
        value = (values[name] or "").strip()
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
    d = {**config.get("download", {}), **config.get("reader", {})}
    db = config.get("database", {})
    values: dict[str, Any] = {
        "concurrency": d.get("concurrency", 64),
        "per_host": d.get("per_host", 2),
        "host_delay_seconds": d.get("host_delay_seconds", 0.25),
        "max_file_bytes": d.get("max_file_bytes", 104857600),
        "timeout_seconds": d.get("request_timeout_seconds", 60),
        "retries": d.get("retries", 3),
        "max_redirects": d.get("max_redirects", 20),
        "chunk_bytes": d.get("chunk_bytes", 262144),
        "checkpoint_bytes": d.get("checkpoint_bytes", 1048576),
        "local_workers": d.get("local_workers", 2),
        "cloud_workers": d.get("cloud_workers", 1),
        "cloud_poll_seconds": d.get("cloud_poll_seconds", 5),
        "cloud_timeout_seconds": d.get("cloud_timeout_seconds", 1800),
        "mongo_uri": os.environ.get("MONGO_URI")
        or db.get("mongo_uri", "mongodb://127.0.0.1:27017"),
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
    return Settings(**values)


def project_paths(root: Path | None = None) -> ProjectPaths:
    """Return the paths of ``root``, defaulting to the directory holding ``pyproject.toml``."""
    return ProjectPaths(root if root is not None else Path(__file__).resolve().parents[4])
