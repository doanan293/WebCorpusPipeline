from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.cli import context
from data_pipeline.shared.domain.config import ProjectPaths


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectPaths:
    """A throw-away project directory that the CLI treats as the pipeline root."""
    root = tmp_path / "project"
    root.mkdir()
    paths = ProjectPaths(root)
    monkeypatch.setattr(context, "current_paths", lambda: paths)
    return paths
