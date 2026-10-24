from __future__ import annotations

from pathlib import Path

from crawler.domain.shared.config import ProjectPaths
from crawler.infra.shared.settings import project_paths


def test_default_root_is_the_pipeline_directory() -> None:
    paths = project_paths()

    assert (paths.root / "pyproject.toml").is_file()
    assert paths.corpus_pulled == paths.root / "data/corpus/pulled"


def test_paths_are_derived_from_the_given_root(tmp_path: Path) -> None:
    paths = ProjectPaths(tmp_path)

    assert paths.env_file == tmp_path / ".env"
    assert paths.pipeline_config == tmp_path / "config/pipeline.yaml"
    assert paths.kaggle_config == tmp_path / "config/kaggle-dataset.json"
    assert paths.corpus_lock == tmp_path / "config/corpus.lock.json"
    assert paths.domains_all == tmp_path / "config/domains/all.txt"
    assert paths.domains_active == tmp_path / "config/domains/active.txt"
    assert paths.domains_status == tmp_path / "config/domains/status.jsonl"
    assert paths.corpus_pulled == tmp_path / "data/corpus/pulled"
