from __future__ import annotations

from typing import Any

from data_pipeline.application.storage.ports import CorpusStore
from data_pipeline.domain.storage.documents import to_write


def store_record(record: dict[str, Any], store: CorpusStore, now_iso: str) -> None:
    write = to_write(record, now_iso)
    store.upsert(write.collection, write.document)
    store.replace_edges(record["url"], write.edges)
