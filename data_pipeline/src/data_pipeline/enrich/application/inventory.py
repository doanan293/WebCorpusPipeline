from __future__ import annotations

from data_pipeline.enrich.application.ports import ArticleSource
from data_pipeline.enrich.domain.inventory_rules import clean_url, page_refs
from data_pipeline.enrich.domain.models import InventorySummary, SourceProgress, SourceRef
from data_pipeline.enrich.infrastructure.sqlite.work_state import WorkState


def _summary(state: WorkState) -> InventorySummary:
    return InventorySummary(
        state.conn.execute("SELECT count(*) FROM inventory").fetchone()[0],
        state.conn.execute("SELECT count(*) FROM refs").fetchone()[0],
    )


def index_inventory(
    source: ArticleSource, state: WorkState, batch_size: int = 500
) -> InventorySummary:
    size, mtime_ns = source.stat()
    key = source.key
    stored = state.source_progress(key)
    same_file = stored is not None and (stored.size, stored.mtime_ns) == (size, mtime_ns)
    if same_file and stored is not None and stored.done:
        return _summary(state)
    # A rewritten file starts over: a stored byte offset would land mid-line.
    offset = stored.cursor if same_file and stored is not None else 0
    if offset == 0:
        modified = source.modified_pages()
        if modified:
            state.delete_refs_from(modified)

    def progress(cursor: int, *, done: bool = False) -> SourceProgress:
        return SourceProgress(key, size, mtime_ns, cursor, done)

    candidates: set[str] = set()
    refs: list[SourceRef] = []
    cursor = offset
    for row, cursor in source.rows(offset):
        page = clean_url(str(row.get("url") or row.get("final_url") or ""))
        if not page:
            continue
        found = page_refs(row, page)
        refs.extend(found)
        candidates.update(ref.target_url for ref in found)
        if cursor - offset >= batch_size * 1024:
            state.add_inventory_batch(sorted(candidates), refs, progress(cursor))
            candidates.clear()
            refs.clear()
            offset = cursor
    state.add_inventory_batch(sorted(candidates), refs, progress(cursor, done=True))
    return _summary(state)
