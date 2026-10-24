from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from crawler.domain.merger.config import MergerConfig
from crawler.domain.shared.errors import ConfigError

_RETIRED = {
    "allowed_suffixes": "links are followed only within the hosts of the seeds",
    "hosts_file": "renamed seeds_file (hostnames or URLs, one per line)",
}


def load_merger_config(path: Path) -> MergerConfig:
    """Read ``merger.yaml``; a missing or unusable value is a ``ConfigError`` naming it."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path} must hold a mapping"
        raise ConfigError(msg)
    values = cast("dict[str, Any]", raw)
    defaults = MergerConfig()
    batch_size = values.get("batch_size", defaults.batch_size)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
        msg = "batch_size must be a positive integer"
        raise ConfigError(msg)
    for retired, instead in _RETIRED.items():
        if retired in values:
            msg = f"{retired} is no longer read: {instead}"
            raise ConfigError(msg)
    try:
        return MergerConfig(
            port=int(values.get("port", defaults.port)),
            seeds_file=Path(str(values.get("seeds_file", defaults.seeds_file))),
            initial_scope_file=Path(
                str(values.get("initial_scope_file", defaults.initial_scope_file))
            ),
            batch_size=batch_size,
            batch_seconds=float(values.get("batch_seconds", defaults.batch_seconds)),
            outbox_retry_seconds=float(
                values.get("outbox_retry_seconds", defaults.outbox_retry_seconds)
            ),
            database=Path(str(values.get("database", defaults.database))),
            outbox=Path(str(values.get("outbox", defaults.outbox))),
        )
    except (TypeError, ValueError) as exc:
        msg = f"invalid merger setting: {exc}"
        raise ConfigError(msg) from exc
