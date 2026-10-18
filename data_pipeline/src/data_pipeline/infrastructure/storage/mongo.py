from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.database import Database
from pymongo.errors import (
    ConnectionFailure,
    DocumentTooLarge,
    ServerSelectionTimeoutError,
    WriteError,
)

from data_pipeline.application.storage.ports import StoreRejectedError, StoreUnavailableError

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
        """Write the page's edges, then drop the ones it no longer has.

        In that order, a crash in between leaves extra edges until the next visit, never a page
        with none.
        """
        unique = list({(e["dest"], e["text"]): e for e in edges}.values())

        def replace() -> None:
            if unique:
                self.db.graph.bulk_write(
                    [
                        ReplaceOne(
                            {"src": src, "dest": e["dest"], "text": e["text"]}, dict(e), upsert=True
                        )
                        for e in unique
                    ],
                    ordered=False,
                )
                keep = [{"dest": e["dest"], "text": e["text"]} for e in unique]
                self.db.graph.delete_many({"src": src, "$nor": keep})
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
