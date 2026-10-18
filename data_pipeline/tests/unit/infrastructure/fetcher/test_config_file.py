from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import ConfigError
from data_pipeline.infrastructure.fetcher.config_file import load_fetcher_config


def test_fetcher_config_reads_repo_file() -> None:
    config = load_fetcher_config(Path("config/fetcher.yaml"))
    assert config.port == 8800
    assert config.max_response_bytes == 104857600
    assert config.timeout_seconds == 60
    assert config.allow_private_addresses is False


def test_fetcher_config_rejects_bad_number(tmp_path: Path) -> None:
    path = tmp_path / "f.yaml"
    path.write_text("timeout_seconds: soon\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="timeout_seconds"):
        load_fetcher_config(path)


def test_fetcher_config_rejects_unknown_key(tmp_path: Path) -> None:
    path = tmp_path / "f.yaml"
    path.write_text("concurrent_requests: 64\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="concurrent_requests"):
        load_fetcher_config(path)
