from __future__ import annotations

from collections.abc import Callable
from typing import Any

from bson import ObjectId
from pymongo import ASCENDING, MongoClient, UpdateOne
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
    """The ``CorpusStore`` port over the html, picture, other and graph collections."""

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
            for edge in {(e["dest"], e["intro"]): e for e in edges}.values()
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

    def existing_html(self, urls: list[str]) -> set[str]:
        found: set[str] = set()
        self._guard(
            lambda: found.update(
                str(doc["url"])
                for doc in self.db.html.find({"url": {"$in": urls}}, {"url": 1, "_id": 0})
            )
        )
        return found

    def link_texts(self, dests: list[str]) -> dict[str, dict[str, int]]:
        """Counted in Mongo: one row per distinct (dest, text), not one per edge."""
        counts: dict[str, dict[str, int]] = {}

        def run() -> None:
            pipeline: list[dict[str, Any]] = [
                {"$match": {"dest": {"$in": dests}}},
                {"$group": {"_id": {"d": "$dest", "i": "$intro"}, "n": {"$sum": 1}}},
            ]
            for row in self.db.graph.aggregate(pipeline, allowDiskUse=True):
                texts = counts.setdefault(str(row["_id"]["d"]), {})
                texts[str(row["_id"].get("i") or "")] = int(row["n"])

        if dests:
            self._guard(run)
        return counts

    def set_intros(self, intros: dict[str, str]) -> None:
        writes = [
            UpdateOne({"url": url}, {"$set": {"intro": intro}}) for url, intro in intros.items()
        ]
        if writes:
            self._guard(lambda: self.db.html.bulk_write(writes, ordered=False))

    def add_pictures(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        writes = [
            UpdateOne({"url": row["url"]}, {"$setOnInsert": row}, upsert=True)
            for row in {row["url"]: row for row in rows}.values()
        ]
        self._guard(lambda: self.db.picture.bulk_write(writes, ordered=False))

    def first_linking_page(self, dest: str) -> str:
        found: list[str] = []

        def run() -> None:
            sources = self.db.graph.distinct("src", {"dest": dest})
            first = self.db.html.find_one({"url": {"$in": sources}}, {"url": 1}, sort=[("_id", 1)])
            found.append(str(first["url"]) if first else "")

        self._guard(run)
        return found[0] if found else ""

    def origin(self, url: str) -> str | None:
        found: list[dict[str, Any] | None] = []
        self._guard(lambda: found.append(self.db.other.find_one({"url": url}, {"origin": 1})))
        doc = found[0] if found else None
        return None if doc is None or "origin" not in doc else str(doc["origin"])

    def touch(self, url: str, checked_at: str) -> None:
        def run() -> None:
            for name in ("html", "other"):
                if (
                    self.db[name]
                    .update_one({"url": url}, {"$set": {"checked_at": checked_at}})
                    .matched_count
                ):
                    return

        self._guard(run)

    def close(self) -> None:
        self.db.client.close()

    def ensure_indexes(self) -> None:
        def create() -> None:
            for collection in ("html", "other", "picture"):
                self.db[collection].create_index([("url", ASCENDING)], unique=True)
            self.db.graph.create_index([("src", ASCENDING)])
            self.db.graph.create_index([("dest", ASCENDING)])

        self._guard(create)


class MongoPictureStore:
    """The ``PictureStore`` port over the ``picture`` collection."""

    def __init__(self, db: Database[dict[str, Any]]) -> None:
        self.db = db

    def pending(self, *, retry_errors: bool) -> list[str]:
        query: dict[str, Any] = {"b64": ""}
        if not retry_errors:
            query["error"] = None
        return [str(doc["url"]) for doc in self.db.picture.find(query, {"url": 1, "_id": 0})]

    def save_picture(
        self,
        url: str,
        *,
        b64: str,
        fmt: str,
        size: int | None,
        error: str | None,
        fetched_at: str,
    ) -> None:
        fields = {"b64": b64, "size": size, "error": error, "fetched_at": fetched_at}
        if fmt:
            fields["format"] = fmt
        fields["updated_at"] = fetched_at
        self.db.picture.update_one({"url": url}, {"$set": fields})

    def close(self) -> None:
        self.db.client.close()
