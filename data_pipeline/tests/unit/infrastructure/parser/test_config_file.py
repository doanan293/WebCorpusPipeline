from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import ConfigError
from data_pipeline.infrastructure.parser.config_file import load_parser_config


def test_parser_config_reads_repo_file() -> None:
    config = load_parser_config(Path("config/parser.yaml"))
    assert config.port == 6600
    assert config.llamaparse is False
    assert config.cache_dir == Path("data/enrichment/cache")


def test_parser_config_rejects_bad_flag(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("llamaparse: maybe\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="llamaparse"):
        load_parser_config(path)
