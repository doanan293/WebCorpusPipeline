from __future__ import annotations

from pathlib import Path

import pytest

from crawler.domain.shared.errors import ConfigError
from crawler.infra.reader.config_file import load_reader_config


def test_reader_config_reads_repo_file() -> None:
    config = load_reader_config(Path("config/reader.yaml"))
    assert config.port == 6600
    assert config.llamaparse is True
    assert config.cache_dir == Path("data/enrichment/cache")


def test_reader_config_rejects_bad_flag(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("llamaparse: maybe\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="llamaparse"):
        load_reader_config(path)
