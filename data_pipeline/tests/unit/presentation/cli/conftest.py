from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.config import ProjectPaths
from data_pipeline.presentation.cli import context


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectPaths:
    """A throw-away project directory that the CLI treats as the pipeline root."""
    root = tmp_path / "project"
    root.mkdir()
    paths = ProjectPaths(root)
    monkeypatch.setattr(context, "current_paths", lambda: paths)
    return paths
