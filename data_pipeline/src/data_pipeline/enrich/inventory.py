from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from urllib.parse import urldefrag, urlsplit

from data_pipeline.enrich.models import Candidate, InventorySummary, SourceRef
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
IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".avif"}


def _clean(value: str) -> str:
    return urldefrag(value.strip())[0]


def _hint(url: str) -> str | None:
    suffix = Path(urlsplit(url).path.lower()).suffix
    return mimetypes.guess_type(url)[0] or (
        "application/" + suffix[1:] if suffix in DOC_EXT else None
    )


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


def index_inventory(source: Path, state: WorkState, batch_size: int = 500) -> InventorySummary:
    articles = source / "articles.jsonl" if source.is_dir() else source
    if not articles.exists():
        raise FileNotFoundError(articles)
    key = source_key(source)
    offset = state.cursor(key)
    if offset == 0 and not state.inventory_done(key):
        modified = _modified_pages(articles.parent / "changes.jsonl")
        if modified:
            state.delete_refs_from(modified)
    candidates: dict[str, Candidate] = {}
    refs: list[SourceRef] = []
    with articles.open("rb") as stream:
        stream.seek(offset)
        while True:
            line = stream.readline()
            if not line:
                break
            row = json.loads(line)
            page = _clean(str(row.get("url") or row.get("final_url") or ""))
            if not page:
                continue
            for link in row.get("links") or []:
                if not isinstance(link, dict) or not link.get("url"):
                    continue
                target = _clean(str(link["url"]))
                refs.append(SourceRef(page, target, "hyperlink", str(link.get("text") or "")))
                if _is_doc(target):
                    candidates.setdefault(target, Candidate(target, _hint(target)))
            for asset in row.get("assets") or []:
                if not isinstance(asset, dict) or not asset.get("url"):
                    continue
                target = _clean(str(asset["url"]))
                ext = Path(urlsplit(target).path.lower()).suffix
                relation = (
                    "image" if ext in IMG_EXT or asset.get("role") == "inline_image" else "asset"
                )
                refs.append(
                    SourceRef(
                        page,
                        target,
                        relation,
                        "",
                        str(asset.get("alt") or ""),
                        str(asset.get("caption") or ""),
                    )
                )
                if _is_doc(target):
                    candidates.setdefault(target, Candidate(target, _hint(target)))
            if stream.tell() - offset >= batch_size * 1024:
                state.add_inventory_batch(
                    list(candidates.values()), refs, stream.tell(), source_key=key
                )
                candidates.clear()
                refs.clear()
                offset = stream.tell()
        state.add_inventory_batch(
            list(candidates.values()), refs, stream.tell(), done=True, source_key=key
        )
    return InventorySummary(
        state.conn.execute("SELECT count(*) FROM inventory").fetchone()[0],
        state.conn.execute("SELECT count(*) FROM refs").fetchone()[0],
        True,
    )
