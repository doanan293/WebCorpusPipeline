from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import ConfigError
from data_pipeline.infrastructure.aggregator.config_file import load_aggregator_config


def test_aggregator_config_reads_repo_file() -> None:
    config = load_aggregator_config(Path("config/aggregator.yaml"))
    assert config.port == 3300
    assert config.allowed_suffixes == ("hust.edu.vn",)
    assert (config.batch_size, config.batch_seconds, config.outbox_retry_seconds) == (
        500,
        1.0,
        30.0,
    )
    assert config.hosts_file == Path("config/domains/active.txt")


def test_aggregator_config_rejects_zero_batch(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("batch_size: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="batch_size"):
        load_aggregator_config(path)
