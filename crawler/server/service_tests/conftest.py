from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from data_pipeline.domain.shared.config import ProjectPaths
from services import cli

_SECRET_NAMES = re.compile(r"LLAMAPARSE_KEY\d+|KAGGLE_.*|CRAWLER_CONTACT")


@pytest.fixture(autouse=True)
def isolated_environment() -> Iterator[None]:
    """Keep real credentials out of tests and undo environment changes made by a test."""
    with mock.patch.dict(os.environ):
        for name in [name for name in os.environ if _SECRET_NAMES.fullmatch(name)]:
            del os.environ[name]
        yield


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectPaths:
    """A throw-away project directory that the services treat as the pipeline root."""
    root = tmp_path / "project"
    root.mkdir()
    paths = ProjectPaths(root)
    monkeypatch.setattr(cli, "current_paths", lambda: paths)
    return paths
