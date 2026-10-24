from __future__ import annotations

from pathlib import Path

import pytest

from data_pipeline.domain.shared.errors import ConfigError
from data_pipeline.infrastructure.mediator.config_file import (
    load_crawl_config,
    load_mediator_config,
)


def test_config_reads_repo_file() -> None:
    config = load_mediator_config(Path("config/mediator.yaml"))
    assert config.port == 5200
    assert config.downloaders == ("http://downloader-1:8800", "http://downloader-2:8801")
    assert config.database == Path("data/state/mediator.sqlite3")
    assert dict(config.recrawl_days) == {"hub": 1, "page": 7, "document": 30}
    assert config.per_host_concurrency == 2
    assert config.autothrottle is True
    assert config.autothrottle_max_delay_seconds == 5.0


def test_config_rejects_missing_downloaders(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("port: 5200\ndatabase: q.sqlite3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="downloaders"):
        load_mediator_config(path)


def test_config_rejects_unknown_kind(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "downloaders: [http://a:8800]\ndatabase: q.sqlite3\nrecrawl_days: {news: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="recrawl_days"):
        load_mediator_config(path)


def test_config_rejects_negative_delay(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "downloaders: [http://a:8800]\ndatabase: q.sqlite3\nhost_delay_seconds: -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="host_delay_seconds"):
        load_mediator_config(path)


def test_config_rejects_downloader_without_scheme(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("downloaders: ['10.0.0.11:8800']\ndatabase: q.sqlite3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"10\.0\.0\.11:8800"):
        load_mediator_config(path)


def test_config_reads_throttle_settings(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(
        "downloaders: [http://a:8800]\ndatabase: q.sqlite3\nper_host_concurrency: 4\n"
        "autothrottle: false\nautothrottle_start_delay_seconds: 0.5\n"
        "autothrottle_max_delay_seconds: 9\nautothrottle_target_concurrency: 2\n",
        encoding="utf-8",
    )
    config = load_mediator_config(path)
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
        load_mediator_config(path)


def test_crawl_config_overrides_mediator_settings(tmp_path: Path) -> None:
    crawl = tmp_path / "crawl.yaml"
    crawl.write_text(
        "workers: 3\nper_host_concurrency: 4\nhost_delay_seconds: 0\ndownloaders: [http://x:1]\n",
        encoding="utf-8",
    )
    config = load_crawl_config(crawl, Path("config/mediator.yaml"))
    assert config.workers == 3
    assert config.mediator.downloaders == ("local:0", "local:1", "local:2")
    assert config.mediator.per_host_concurrency == 4
    assert config.mediator.host_delay_seconds == 0.0
    assert config.mediator.database == Path("data/state/mediator.sqlite3")


def test_repo_crawl_config_uses_the_old_crawler_pace() -> None:
    config = load_crawl_config(Path("config/crawl.yaml"), Path("config/mediator.yaml"))
    assert config.workers == 64
    assert config.mediator.per_host_concurrency == 4
    assert config.mediator.host_delay_seconds == 0.0
    assert config.mediator.autothrottle_start_delay_seconds == 0.25


def test_crawl_config_rejects_zero_workers(tmp_path: Path) -> None:
    crawl = tmp_path / "crawl.yaml"
    crawl.write_text("workers: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="workers"):
        load_crawl_config(crawl, Path("config/mediator.yaml"))
