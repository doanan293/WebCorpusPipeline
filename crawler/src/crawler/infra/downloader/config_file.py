from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any, cast

import yaml

from crawler.domain.downloader.models import DownloaderConfig
from crawler.domain.shared.errors import ConfigError


def load_downloader_config(path: Path) -> DownloaderConfig:
    """Read ``downloader.yaml``; unknown keys and values of the wrong type are ``ConfigError``."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise ConfigError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path} must hold a mapping"
        raise ConfigError(msg)
    values = cast("dict[str, Any]", raw)
    defaults = DownloaderConfig()
    known = {field.name for field in fields(DownloaderConfig)}
    unknown = sorted(set(values) - known)
    if unknown:
        msg = f"unknown downloader settings: {', '.join(unknown)}"
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
    return DownloaderConfig(**values)
