from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from data_pipeline.application.enrich.ports import EnrichUnitOfWork
from data_pipeline.domain.shared.config import Settings

type WorkStateOpener = Callable[..., AbstractContextManager[EnrichUnitOfWork]]
"""Opens ``(work_path, crawl_id, *, rebind)``; e.g. ``SqliteEnrichUnitOfWork.open``."""


@contextmanager
def open_work_state(
    settings: Settings, *, rebind: bool, crawl_id: str | None, opener: WorkStateOpener
) -> Iterator[EnrichUnitOfWork]:
    """Open the enrichment work directory bound to the crawl that produced the source."""
    if crawl_id is None:
        msg = f"source has no crawl_id in README.json or summary.json: {settings.source_path}"
        raise ValueError(msg)
    work_path: Path = settings.work_path
    with opener(work_path, crawl_id, rebind=rebind) as uow:
        uow.recover()
        yield uow
