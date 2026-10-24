from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import (
    ConnectionFailure,
    DocumentTooLarge,
    ServerSelectionTimeoutError,
    WriteError,
)

from crawler.application.storage.ports import StoreRejectedError, StoreUnavailableError

_UNAVAILABLE = (ConnectionFailure, ServerSelectionTimeoutError)


def connect(uri: str) -> MongoClient[dict[str, Any]]:
    return MongoClient(uri, serverSelectionTimeoutMS=5000)


class MongoCorpusStore:
    """The ``CorpusStore`` port over the slide's ``html``, ``other`` and ``graph`` collections."""

    def __init__(self, db: Database[dict[str, Any]]) -> None:
        self.db = db

    def _guard(self, action: Callable[[], object]) -> None:
        try:
            action()
        except _UNAVAILABLE as exc:
            msg = "mongo unavailable"
            raise StoreUnavailableError(msg) from exc
        except (DocumentTooLarge, WriteError) as exc:
            msg = f"mongo refused the record: {exc}"
            raise StoreRejectedError(msg) from exc

    def upsert(self, collection: str, document: dict[str, Any]) -> None:
        self._guard(
            lambda: self.db[collection].replace_one({"url": document["url"]}, document, upsert=True)
        )

    def replace_edges(self, src: str, edges: list[dict[str, Any]]) -> None:
        """Write the page's edges, then drop its other edges.

        New edges get their ids here, so the old ones are exactly those of ``src`` with another id.
        In that order, a crash in between leaves extra edges until the next visit, never a page
        with none; and it takes two writes per page, not one per edge.
        """
        unique = [
            {"_id": ObjectId(), **edge}
            for edge in {(e["dest"], e["text"]): e for e in edges}.values()
        ]

        def replace() -> None:
            if unique:
                self.db.graph.insert_many(unique, ordered=False)
                self.db.graph.delete_many(
                    {"src": src, "_id": {"$nin": [edge["_id"] for edge in unique]}}
                )
            else:
                self.db.graph.delete_many({"src": src})

        self._guard(replace)

    def close(self) -> None:
        self.db.client.close()

    def ensure_indexes(self) -> None:
        def create() -> None:
            for collection in ("html", "other"):
                self.db[collection].create_index([("url", ASCENDING)], unique=True)
            self.db.graph.create_index([("src", ASCENDING)])
            self.db.graph.create_index([("dest", ASCENDING)])

        self._guard(create)
