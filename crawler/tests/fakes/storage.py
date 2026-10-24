"""In-memory stand-in for the storage port."""

from __future__ import annotations

from typing import Any

from crawler.application.storage.ports import StoreRejectedError, StoreUnavailableError

_DOWN = "mongo unavailable"


class FakeCorpusStore:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {
            "html": {},
            "other": {},
            "picture": {},
        }
        self.graph: list[dict[str, Any]] = []
        self.intro_batches: list[dict[str, str]] = []
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

    def existing_html(self, urls: list[str]) -> set[str]:
        return {url for url in urls if url in self.collections["html"]}

    def link_texts(self, dests: list[str]) -> dict[str, dict[str, int]]:
        counts: dict[str, dict[str, int]] = {}
        for edge in self.graph:
            if edge["dest"] in dests:
                texts = counts.setdefault(edge["dest"], {})
                texts[edge["intro"]] = texts.get(edge["intro"], 0) + 1
        return counts

    def set_intros(self, intros: dict[str, str]) -> None:
        self.intro_batches.append(dict(intros))
        for url, intro in intros.items():
            self.collections["html"][url]["intro"] = intro

    def add_pictures(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            self.collections["picture"].setdefault(row["url"], row)

    def first_linking_page(self, dest: str) -> str:
        sources = {edge["src"] for edge in self.graph if edge["dest"] == dest}
        # Dict order stands in for Mongo's _id order: the page stored first comes first.
        return next((url for url in self.collections["html"] if url in sources), "")

    def origin(self, url: str) -> str | None:
        doc = self.collections["other"].get(url)
        return None if doc is None else doc.get("origin")

    def touch(self, url: str, checked_at: str) -> None:
        for name in ("html", "other"):
            if url in self.collections[name]:
                self.collections[name][url]["checked_at"] = checked_at

    def ensure_indexes(self) -> None:
        return None

    def close(self) -> None:
        return None
