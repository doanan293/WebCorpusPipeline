"""In-memory stand-in for the storage port."""

from __future__ import annotations

from typing import Any

from crawler.application.storage.ports import StoreRejectedError, StoreUnavailableError

_DOWN = "mongo unavailable"


class FakeCorpusStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {"html": {}, "other": {}}
        self.graph: list[dict[str, Any]] = []
        self.down = False
        self.reject = False

    def upsert(self, collection: str, document: dict[str, Any]) -> None:
        if self.down:
            raise StoreUnavailableError(_DOWN)
        if self.reject:
            msg = "document too large"
            raise StoreRejectedError(msg)
        self.collections[collection][document["url"]] = document

    def replace_edges(self, src: str, edges: list[dict[str, Any]]) -> None:
        if self.down:
            raise StoreUnavailableError(_DOWN)
        self.graph = [edge for edge in self.graph if edge["src"] != src] + edges

    def incoming_intros(self, dest: str) -> list[str]:
        return [edge["intro"] for edge in self.graph if edge["dest"] == dest]

    def set_intro(self, url: str, intro: str) -> bool:
        doc = self.collections["html"].get(url)
        if doc is None:
            return False
        doc["intro"] = intro
        return True

    def touch(self, url: str, checked_at: str) -> None:
        for name in ("html", "other"):
            if url in self.collections[name]:
                self.collections[name][url]["checked_at"] = checked_at

    def ensure_indexes(self) -> None:
        return None

    def close(self) -> None:
        return None
