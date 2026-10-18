from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import yaml

from data_pipeline.domain.fetcher.models import FetcherConfig
from data_pipeline.domain.shared.errors import ConfigError


def load_fetcher_config(path: Path) -> FetcherConfig:
    """Read ``fetcher.yaml``; unknown keys and values of the wrong type are ``ConfigError``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path} must hold a mapping"
        raise ConfigError(msg)
    values = cast("dict[str, Any]", raw)
    defaults = FetcherConfig()
    known = {field.name for field in fields(FetcherConfig)}
    unknown = sorted(set(values) - known)
    if unknown:
        msg = f"unknown fetcher settings: {', '.join(unknown)}"
        raise ConfigError(msg)
    for name, value in list(values.items()):
        expected = type(getattr(defaults, name))
        if expected is tuple:
            if not isinstance(value, list) or not all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in cast("list[object]", value)
            ):
                msg = f"{name} must be a list of numbers"
                raise ConfigError(msg)
            values[name] = tuple(cast("list[int]", value))
            continue
        ok = isinstance(value, expected) or (expected is float and isinstance(value, int))
        if not ok or (isinstance(value, bool) and expected is not bool):
            msg = f"{name} must be a {expected.__name__}"
            raise ConfigError(msg)
    return FetcherConfig(**values)
