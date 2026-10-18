"""In-memory stand-in for the storage port."""

from __future__ import annotations

from typing import Any

from data_pipeline.application.storage.ports import StoreUnavailableError

_DOWN = "mongo unavailable"


class FakeCorpusStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {"html": {}, "other": {}}
        self.graph: list[dict[str, Any]] = []
        self.down = False

    def upsert(self, collection: str, document: dict[str, Any]) -> None:
        if self.down:
            raise StoreUnavailableError(_DOWN)
        self.collections[collection][document["url"]] = document

    def replace_edges(self, src: str, edges: list[dict[str, Any]]) -> None:
        if self.down:
            raise StoreUnavailableError(_DOWN)
        self.graph = [edge for edge in self.graph if edge["src"] != src] + edges

    def ensure_indexes(self) -> None:
        return None
