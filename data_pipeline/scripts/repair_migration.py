"""One-off: repair what the migration of the batch crawl got wrong (review findings C1, C2).

C1. The aggregator's content owners were imported under the request URL, while records arrive
    under the final URL: a recrawled page would be taken for a duplicate of itself and emptied.
    Owners move to their final URL.
C2. Mongo ``html`` lost content where an empty duplicate shared the owner's final URL and was
    written last, and no duplicate got ``duplicate_of``. Only damaged docs are changed, and only
    docs the migration wrote (``updated_at`` equal to it): pages fetched since are left alone.

Reads ``<crawl>/state/index.sqlite3`` read-only. ``--dry-run`` prints what would change. Delete
after use.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any

from pymongo import MongoClient, UpdateOne

from data_pipeline import bootstrap
from data_pipeline.domain.parser.records import content_id_for, record_from_article
from data_pipeline.domain.storage.documents import to_write

_BATCH = 5_000


@dataclass
class RepairReport:
    filled: int = 0
    duplicate_of_set: int = 0
    skipped_newer: int = 0


@dataclass
class HtmlPlan:
    updates: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    report: RepairReport = field(default_factory=RepairReport)


def _articles(legacy: sqlite3.Connection) -> list[dict[str, Any]]:
    return [json.loads(payload) for (payload,) in legacy.execute("SELECT payload FROM articles")]


def final_urls(legacy: sqlite3.Connection) -> dict[str, str]:
    """Request URL → final URL of every stored page."""
    return {a["url"]: a.get("final_url") or a["url"] for a in _articles(legacy)}


def repair_owners(state: sqlite3.Connection, finals: Mapping[str, str]) -> int:
    """C1: move each content owner to its final URL; return how many moved."""
    moves = [
        (finals[url], content_id)
        for content_id, url in state.execute("SELECT content_id, url FROM content_owners")
        if url in finals and finals[url] != url
    ]
    with state:
        state.executemany("UPDATE content_owners SET url = ? WHERE content_id = ?", moves)
    return len(moves)


def _text(article: Mapping[str, Any]) -> str:
    return str(article.get("text") or "")


def _expected(
    final: str, group: list[dict[str, Any]], owners: Mapping[str, str], finals: Mapping[str, str]
) -> tuple[str, Any] | None:
    """What the doc at ``final`` should be: ``("content", record)``, ``("duplicate", owner)``."""
    for article in group:
        if not _text(article).strip():
            continue
        cid = article.get("content_id") or content_id_for(_text(article))
        if owners.get(cid, article["url"]) == article["url"]:
            return "content", record_from_article(final, article)
    for article in group:
        owner = article.get("duplicate_of")
        if owner is None and _text(article).strip():
            owner = owners.get(article.get("content_id") or content_id_for(_text(article)))
        if owner is not None and finals.get(owner, owner) != final:
            return "duplicate", finals.get(owner, owner)
    return None


def plan_html(
    legacy: sqlite3.Connection, docs: Iterable[Mapping[str, Any]], migrated_at: str
) -> HtmlPlan:
    """C2: the ``$set`` each damaged html doc needs."""
    finals = final_urls(legacy)
    owners = dict(legacy.execute("SELECT content_id, url FROM content_owner"))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in _articles(legacy):
        groups[finals[article["url"]]].append(article)
    plan = HtmlPlan()
    for doc in docs:
        url = doc["url"]
        expected = _expected(url, groups.get(url, []), owners, finals)
        if expected is None or str(doc.get("content") or "").strip():
            continue
        kind, value = expected
        if kind == "duplicate" and doc.get("duplicate_of") == value:
            continue
        if str(doc.get("updated_at") or "") > migrated_at:
            plan.report.skipped_newer += 1
            continue
        if kind == "content":
            fields = to_write(value, migrated_at).document
            fields.pop("url", None)
            fields.pop("updated_at", None)
            plan.updates.append((url, fields))
            plan.report.filled += 1
        else:
            plan.updates.append((url, {"duplicate_of": value}))
            plan.report.duplicate_of_set += 1
    return plan


def _migrated_at(db: Any) -> str:
    """The ``updated_at`` the migration stamped on every doc it wrote (the most common one)."""
    (top,) = db.html.aggregate(
        [{"$group": {"_id": "$updated_at", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 1}]
    )
    return str(top["_id"])


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawl", type=Path, default=Path("data/crawl"))
    parser.add_argument("--state", type=Path, default=Path("data/state/aggregator.sqlite3"))
    parser.add_argument("--mongodb-uri", default="mongodb://127.0.0.1:27017")
    parser.add_argument("--database", default="information_retrieval")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    legacy = sqlite3.connect(f"file:{args.crawl / 'state' / 'index.sqlite3'}?mode=ro", uri=True)
    client: MongoClient[dict[str, Any]] = MongoClient(
        args.mongodb_uri, serverSelectionTimeoutMS=5000
    )
    with bootstrap.state_lease(args.state, "repair_migration"):
        try:
            db = client[args.database]
            migrated_at = _migrated_at(db)
            print(f"migration stamp: {migrated_at}")
            docs = db.html.find(
                {}, {"_id": 0, "url": 1, "content": 1, "duplicate_of": 1, "updated_at": 1}
            )
            plan = plan_html(legacy, docs, migrated_at)
            finals = final_urls(legacy)
            state = sqlite3.connect(args.state)
            moved = sum(
                1
                for content_id, url in state.execute("SELECT content_id, url FROM content_owners")
                if url in finals and finals[url] != url
            )
            print(f"owners to move: {moved}")
            print(f"html: {plan.report}")
            if args.dry_run:
                return 0
            print(f"owners moved: {repair_owners(state, finals)}")
            updates = iter(plan.updates)
            while batch := list(islice(updates, _BATCH)):
                db.html.bulk_write(
                    [UpdateOne({"url": url}, {"$set": fields}) for url, fields in batch],
                    ordered=False,
                )
            print(f"html docs updated: {len(plan.updates)}")
        finally:
            client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
