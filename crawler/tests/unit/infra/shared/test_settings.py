from __future__ import annotations

import os
from pathlib import Path

import pytest

from crawler.domain.shared.config import ProjectPaths
from crawler.infra.shared.settings import load_api_keys, load_settings


def test_settings_read_pipeline_yaml(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/pipeline.yaml").write_text("download:\n  concurrency: 8\n  per_host: 2\n")

    settings = load_settings(ProjectPaths(tmp_path))

    assert settings.concurrency == 8
    assert settings.project_root == tmp_path


def test_keys_are_read_from_the_project_env_file_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in list(os.environ):
        if name.startswith("LLAMAPARSE_KEY"):
            monkeypatch.delenv(name)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("LLAMAPARSE_KEY1=project-key\n")
    (tmp_path / ".env").write_text("LLAMAPARSE_KEY1=parent-key\n")

    assert load_api_keys(project / ".env") == [("LLAMAPARSE_KEY1", "project-key")]


def test_exported_keys_take_precedence_over_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLAMAPARSE_KEY1", "shell-key")
    (tmp_path / ".env").write_text("LLAMAPARSE_KEY1=file-key\n")

    assert load_api_keys(tmp_path / ".env") == [("LLAMAPARSE_KEY1", "shell-key")]
