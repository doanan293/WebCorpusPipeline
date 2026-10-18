from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from data_pipeline.domain.aggregator.config import AggregatorConfig
from data_pipeline.domain.shared.errors import ConfigError


def load_aggregator_config(path: Path) -> AggregatorConfig:
    """Read ``aggregator.yaml``; a missing or unusable value is a ``ConfigError`` naming it."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path} must hold a mapping"
        raise ConfigError(msg)
    values = cast("dict[str, Any]", raw)
    defaults = AggregatorConfig()
    batch_size = values.get("batch_size", defaults.batch_size)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        msg = "batch_size must be a positive integer"
        raise ConfigError(msg)
    suffixes = values.get("allowed_suffixes", list(defaults.allowed_suffixes))
    if not isinstance(suffixes, list):
        msg = "allowed_suffixes must be a list of domains"
        raise ConfigError(msg)
    try:
        return AggregatorConfig(
            port=int(values.get("port", defaults.port)),
            hosts_file=Path(str(values.get("hosts_file", defaults.hosts_file))),
            allowed_suffixes=tuple(str(s).lower() for s in cast("list[object]", suffixes)),
            batch_size=batch_size,
            batch_seconds=float(values.get("batch_seconds", defaults.batch_seconds)),
            outbox_retry_seconds=float(
                values.get("outbox_retry_seconds", defaults.outbox_retry_seconds)
            ),
            database=Path(str(values.get("database", defaults.database))),
            outbox=Path(str(values.get("outbox", defaults.outbox))),
        )
    except (TypeError, ValueError) as exc:
        msg = f"invalid aggregator setting: {exc}"
        raise ConfigError(msg) from exc
