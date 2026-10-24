from __future__ import annotations

from pathlib import Path

from crawler import bootstrap
from crawler.domain.shared.config import ProjectPaths, Settings


def current_paths() -> ProjectPaths:
    """Return the project layout the commands operate on."""
    return bootstrap.project_paths()


def pick(value: Path | None, default: Path) -> Path:
    """Use a path typed by the user as given, otherwise the project default."""
    return value if value is not None else default


def build_settings() -> Settings:
    """``config/pipeline.yaml`` and the environment, for commands that need MongoDB."""
    return bootstrap.load_settings(current_paths())
