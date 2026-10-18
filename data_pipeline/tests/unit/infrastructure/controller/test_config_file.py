from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import ConfigError
from data_pipeline.infrastructure.controller.config_file import load_controller_config


def test_config_reads_repo_file() -> None:
    config = load_controller_config(Path("config/controller.yaml"))
    assert config.port == 5200
    assert config.downloaders == ("http://fetcher-1:8800", "http://fetcher-2:8801")
    assert config.database == Path("data/state/controller.sqlite3")
    assert dict(config.recrawl_days) == {"hub": 1, "page": 7, "document": 30}
    assert config.per_host_concurrency == 2
    assert config.autothrottle is True
    assert config.autothrottle_max_delay_seconds == 5.0


def test_config_rejects_missing_downloaders(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("port: 5200\ndatabase: q.sqlite3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="downloaders"):
        load_controller_config(path)


def test_config_rejects_unknown_kind(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "downloaders: [http://a:8800]\ndatabase: q.sqlite3\nrecrawl_days: {news: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="recrawl_days"):
        load_controller_config(path)


def test_config_rejects_negative_delay(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "downloaders: [http://a:8800]\ndatabase: q.sqlite3\nhost_delay_seconds: -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="host_delay_seconds"):
        load_controller_config(path)


def test_config_rejects_downloader_without_scheme(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("downloaders: ['10.0.0.11:8800']\ndatabase: q.sqlite3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"10\.0\.0\.11:8800"):
        load_controller_config(path)


def test_config_reads_throttle_settings(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "downloaders: [http://a:8800]\ndatabase: q.sqlite3\nper_host_concurrency: 4\n"
        "autothrottle: false\nautothrottle_start_delay_seconds: 0.5\n"
        "autothrottle_max_delay_seconds: 9\nautothrottle_target_concurrency: 2\n",
        encoding="utf-8",
    )
    config = load_controller_config(path)
    assert config.per_host_concurrency == 4
    assert config.autothrottle is False
    assert config.autothrottle_start_delay_seconds == 0.5
    assert config.autothrottle_max_delay_seconds == 9.0
    assert config.autothrottle_target_concurrency == 2.0


@pytest.mark.parametrize(
    "line", ["per_host_concurrency: 0", "per_host_concurrency: 1.5", "autothrottle: yes please"]
)
def test_config_rejects_bad_throttle_settings(tmp_path: Path, line: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        f"downloaders: [http://a:8800]\ndatabase: q.sqlite3\n{line}\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError, match=line.split(":", maxsplit=1)[0]):
        load_controller_config(path)
