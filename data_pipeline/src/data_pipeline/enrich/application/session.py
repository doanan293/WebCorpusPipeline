from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from data_pipeline.enrich.infrastructure.sqlite.work_state import WorkState
from data_pipeline.shared.domain.config import Settings


@contextmanager
def open_work_state(
    settings: Settings, *, rebind: bool, crawl_id: str | None
) -> Iterator[WorkState]:
    """Open the enrichment work directory bound to the crawl that produced the source."""
    if crawl_id is None:
        msg = f"source has no crawl_id in README.json or summary.json: {settings.source_path}"
        raise ValueError(msg)
    with WorkState.open(settings.work_path, crawl_id, rebind=rebind) as state:
        state.recover()
        yield state
