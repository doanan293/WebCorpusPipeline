from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlsplit

from data_pipeline.enrich.models import InventorySummary, SourceProgress, SourceRef
from data_pipeline.enrich.state import WorkState

DOC_EXT = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".odt",
    ".ods",
    ".odp",
    ".rtf",
    ".csv",
}


def _clean(value: str) -> str:
    return urldefrag(value.strip())[0]


def _is_doc(url: str) -> bool:
    return Path(urlsplit(url).path.lower()).suffix in DOC_EXT


def source_key(source: Path) -> str:
    articles = source / "articles.jsonl" if source.is_dir() else source
    return str(articles.resolve())


def _modified_pages(changes_path: Path) -> set[str]:
    if not changes_path.exists():
        return set()
    pages: set[str] = set()
    for line in changes_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        change = json.loads(line)
        if change.get("change") == "modified" and change.get("url"):
            pages.add(_clean(str(change["url"])))
    return pages


def _summary(state: WorkState) -> InventorySummary:
    return InventorySummary(
        state.conn.execute("SELECT count(*) FROM inventory").fetchone()[0],
        state.conn.execute("SELECT count(*) FROM refs").fetchone()[0],
    )


def _page_refs(row: dict[str, Any], page: str) -> list[SourceRef]:
    """Return the links and assets of one page that point at a document candidate."""
    refs: list[SourceRef] = []
    for link in row.get("links") or []:
        if isinstance(link, dict) and link.get("url"):
            target = _clean(str(link["url"]))
            if _is_doc(target):
                refs.append(SourceRef(page, target, "hyperlink", str(link.get("text") or "")))
    for asset in row.get("assets") or []:
        if isinstance(asset, dict) and asset.get("url"):
            target = _clean(str(asset["url"]))
            if _is_doc(target):
                refs.append(
                    SourceRef(
                        page,
                        target,
                        "asset",
                        "",
                        str(asset.get("alt") or ""),
                        str(asset.get("caption") or ""),
                    )
                )
    return refs


def index_inventory(source: Path, state: WorkState, batch_size: int = 500) -> InventorySummary:
    articles = source / "articles.jsonl" if source.is_dir() else source
    if not articles.exists():
        raise FileNotFoundError(articles)
    key = source_key(source)
    stat = articles.stat()
    stored = state.source_progress(key)
    same_file = stored is not None and (stored.size, stored.mtime_ns) == (
        stat.st_size,
        stat.st_mtime_ns,
    )
    if same_file and stored is not None and stored.done:
        return _summary(state)
    # A rewritten file starts over: a stored byte offset would land mid-line.
    offset = stored.cursor if same_file and stored is not None else 0
    if offset == 0:
        modified = _modified_pages(articles.parent / "changes.jsonl")
        if modified:
            state.delete_refs_from(modified)

    def progress(cursor: int, *, done: bool = False) -> SourceProgress:
        return SourceProgress(key, stat.st_size, stat.st_mtime_ns, cursor, done)

    candidates: set[str] = set()
    refs: list[SourceRef] = []
    with articles.open("rb") as stream:
        stream.seek(offset)
        while line := stream.readline():
            row = json.loads(line)
            page = _clean(str(row.get("url") or row.get("final_url") or ""))
            if not page:
                continue
            page_refs = _page_refs(row, page)
            refs.extend(page_refs)
            candidates.update(ref.target_url for ref in page_refs)
            if stream.tell() - offset >= batch_size * 1024:
                state.add_inventory_batch(sorted(candidates), refs, progress(stream.tell()))
                candidates.clear()
                refs.clear()
                offset = stream.tell()
        state.add_inventory_batch(sorted(candidates), refs, progress(stream.tell(), done=True))
    return _summary(state)
