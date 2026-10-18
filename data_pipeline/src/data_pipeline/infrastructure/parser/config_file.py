from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from data_pipeline.domain.parser.config import ParserConfig
from data_pipeline.domain.shared.errors import ConfigError


def load_parser_config(path: Path) -> ParserConfig:
    """Read ``parser.yaml``; a value of the wrong type is a ``ConfigError`` naming it."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path} must hold a mapping"
        raise ConfigError(msg)
    values = cast("dict[str, Any]", raw)
    defaults = ParserConfig()
    llamaparse = values.get("llamaparse", defaults.llamaparse)
    if not isinstance(llamaparse, bool):
        msg = "llamaparse must be true or false"
        raise ConfigError(msg)
    try:
        return ParserConfig(
            port=int(values.get("port", defaults.port)),
            llamaparse=llamaparse,
            cache_dir=Path(str(values.get("cache_dir", defaults.cache_dir))),
            cloud_poll_seconds=float(values.get("cloud_poll_seconds", defaults.cloud_poll_seconds)),
            cloud_timeout_seconds=float(
                values.get("cloud_timeout_seconds", defaults.cloud_timeout_seconds)
            ),
        )
    except (TypeError, ValueError) as exc:
        msg = f"invalid parser setting: {exc}"
        raise ConfigError(msg) from exc
