from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from data_pipeline.core.paths import ProjectPaths
from data_pipeline.core.settings import Settings
from data_pipeline.enrich.inventory import source_key
from data_pipeline.enrich.state import WorkState, fingerprint_source, read_crawl_id


@contextmanager
def open_work_state(settings: Settings, *, rebind: bool) -> Iterator[WorkState]:
    """Open the enrichment work directory bound to the configured crawl source."""
    source = settings.source_path
    with WorkState.open(
        settings.work_path,
        fingerprint_source(source),
        crawl_id=read_crawl_id(source),
        rebind=rebind,
    ) as state:
        state.recover()
        state.migrate_legacy_cursor(source_key(ProjectPaths(settings.project_root).crawl_final))
        yield state
