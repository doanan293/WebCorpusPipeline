from __future__ import annotations

from typing import Any

from crawler.application.storage.ports import CorpusStore
from crawler.domain.storage.documents import to_write
from crawler.domain.storage.intro import build_intro

UNCHANGED = frozenset({"unchanged", "not_modified"})


def store_record(record: dict[str, Any], store: CorpusStore, now_iso: str) -> None:
    """The teacher's three storage tasks: the record, its edges, the intro of what it links to."""
    if record.get("change") in UNCHANGED:
        store.touch(record["url"], now_iso)
        return
    write = to_write(record, now_iso)
    store.upsert(write.collection, write.document)
    store.replace_edges(record["url"], write.edges)
    targets = dict.fromkeys(edge["dest"] for edge in write.edges)
    if write.collection == "html":
        targets = {record["url"]: None, **targets}
    for url in targets:
        store.set_intro(url, build_intro(store.incoming_intros(url)))
