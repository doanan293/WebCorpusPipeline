from __future__ import annotations

import os
from pathlib import Path

import pytest

from data_pipeline.shared.domain.config import ProjectPaths
from data_pipeline.shared.infrastructure.settings import load_api_keys, load_settings


def test_settings_read_pipeline_yaml_and_resolve_data_dirs(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config/pipeline.yaml").write_text("download:\n  concurrency: 8\n  per_host: 2\n")

    settings = load_settings(ProjectPaths(tmp_path))

    assert settings.concurrency == 8
    assert settings.project_root == tmp_path
    assert settings.source_path == tmp_path / "data/crawl/final"
    assert settings.work_path == tmp_path / "data/enrichment"


def test_pipeline_paths_keep_absolute_overrides(tmp_path: Path) -> None:
    source = tmp_path / "external-source"
    work = tmp_path / "external-work"

    settings = load_settings(ProjectPaths(tmp_path), {"source": source, "work_dir": work})

    assert settings.source_path == source
    assert settings.work_path == work


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
