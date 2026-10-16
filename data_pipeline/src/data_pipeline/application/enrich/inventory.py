from __future__ import annotations

from data_pipeline.application.enrich.ports import ArticleSource, Inventory
from data_pipeline.domain.enrich.inventory_rules import clean_url, page_refs
from data_pipeline.domain.enrich.models import InventorySummary, SourceProgress, SourceRef


def index_inventory(
    source: ArticleSource, inventory: Inventory, batch_size: int = 500
) -> InventorySummary:
    size, mtime_ns = source.stat()
    key = source.key
    stored = inventory.source_progress(key)
    same_file = stored is not None and (stored.size, stored.mtime_ns) == (size, mtime_ns)
    if same_file and stored is not None and stored.done:
        return inventory.inventory_counts()
    # A rewritten file starts over: a stored byte offset would land mid-line.
    offset = stored.cursor if same_file and stored is not None else 0
    if offset == 0:
        modified = source.modified_pages()
        if modified:
            inventory.delete_refs_from(modified)

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
            inventory.add_inventory_batch(sorted(candidates), refs, progress(cursor))
            candidates.clear()
            refs.clear()
            offset = cursor
    inventory.add_inventory_batch(sorted(candidates), refs, progress(cursor, done=True))
    return inventory.inventory_counts()
