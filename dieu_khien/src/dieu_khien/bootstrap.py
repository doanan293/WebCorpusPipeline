"""The composition root: builds the adapters the service needs."""

from __future__ import annotations

import time
from pathlib import Path

from dieu_khien.application.controller.ports import Clock, Downloader, UrlQueue
from dieu_khien.domain.controller.models import ControllerConfig
from dieu_khien.infrastructure.controller.config_file import load_controller_config
from dieu_khien.infrastructure.controller.http_downloader import HttpDownloader
from dieu_khien.infrastructure.controller.sqlite_queue import SqliteUrlQueue


def controller_config(path: Path) -> ControllerConfig:
    return load_controller_config(path)


def url_queue(path: Path) -> UrlQueue:
    return SqliteUrlQueue(path)


def downloader(config: ControllerConfig) -> Downloader:
    return HttpDownloader(
        ping_timeout=config.ping_timeout_seconds, load_timeout=config.load_timeout_seconds
    )


class _SystemClock:
    def now(self) -> float:
        return time.time()


def system_clock() -> Clock:
    return _SystemClock()
