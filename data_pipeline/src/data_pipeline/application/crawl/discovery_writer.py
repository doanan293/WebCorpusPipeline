from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from data_pipeline.application.crawl.ports import CrawlUnitOfWork


class DiscoveryWriter:
    def __init__(self, root: Path, uow: CrawlUnitOfWork) -> None:
        self.root = root
        self._uow = uow
        self.urls = uow.urls
        self.hosts = uow.hosts
        self.run = uow.run

    def record_target(self, record: dict[str, object]) -> None:
        self.hosts.put_target(record)

    def record_probe(self, record: dict[str, object]) -> None:
        self.urls.upsert_url(record)

    def write_error(self, record: dict[str, object]) -> None:
        self.urls.complete_url(
            {"url": record["url"], "status": "failed", "reason": record["error"]},
            error=record,
        )

    def checkpoint(self, url: str) -> None:
        self.urls.checkpoint(url)

    def publish(self) -> None:
        self.run.export_discovery(self.root)

    def close(self) -> None:
        self._uow.close()
