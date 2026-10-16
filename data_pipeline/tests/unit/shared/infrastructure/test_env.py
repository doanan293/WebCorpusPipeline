from __future__ import annotations

import os
from pathlib import Path

import pytest

from data_pipeline.shared.domain.config import ProjectPaths
from data_pipeline.shared.infrastructure.env import load_env


def test_load_env_reads_values_from_the_project_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRAWLER_CONTACT", raising=False)
    (tmp_path / ".env").write_text("CRAWLER_CONTACT=file@example.com\n")

    load_env(ProjectPaths(tmp_path))

    assert os.environ["CRAWLER_CONTACT"] == "file@example.com"


def test_load_env_does_not_override_exported_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CRAWLER_CONTACT", "shell@example.com")
    (tmp_path / ".env").write_text("CRAWLER_CONTACT=file@example.com\n")

    load_env(ProjectPaths(tmp_path))

    assert os.environ["CRAWLER_CONTACT"] == "shell@example.com"


def test_load_env_ignores_a_missing_file(tmp_path: Path) -> None:
    load_env(ProjectPaths(tmp_path))
