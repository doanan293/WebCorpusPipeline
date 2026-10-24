from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import ConfigError
from data_pipeline.infrastructure.merger.config_file import load_merger_config


def test_merger_config_reads_repo_file() -> None:
    config = load_merger_config(Path("config/merger.yaml"))
    assert config.port == 3300
    assert (config.batch_size, config.batch_seconds, config.outbox_retry_seconds) == (
        500,
        1.0,
        30.0,
    )
    assert config.seeds_file == Path("config/domains/active.txt")
    assert config.initial_scope_file == Path("config/domains/active.txt")


def test_merger_config_rejects_zero_batch(tmp_path: Path) -> None:
    path = tmp_path / "a.yaml"
    path.write_text("batch_size: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="batch_size"):
        load_merger_config(path)


@pytest.mark.parametrize("key", ["allowed_suffixes", "hosts_file"])
def test_retired_keys_are_rejected(tmp_path: Path, key: str) -> None:
    path = tmp_path / "a.yaml"
    path.write_text(f"{key}: x\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=key):
        load_merger_config(path)
