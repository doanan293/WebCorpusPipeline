"""One-off: seed the controller's queue from the batch crawl, so it continues, not re-crawls.

Reads ``<crawl>/state/index.sqlite3`` and ``<crawl>/manifest.json`` read-only. Delete after use.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from data_pipeline.domain.controller.kinds import url_kind
from data_pipeline.domain.controller.models import DEFAULT_RECRAWL_DAYS, QueuedUrl, UrlKind
from data_pipeline.domain.controller.scheduling import DAY_SECONDS, spread
from data_pipeline.domain.shared.assets import recognizable_asset
from data_pipeline.infrastructure.controller.sqlite_queue import SqliteUrlQueue

_SKIPPED_PURPOSES = frozenset({"robots", "sitemap"})


def spread_due(url: str, sent_at: float, period_seconds: float) -> float:
    return spread(url, sent_at, period_seconds)


def _period(kind: UrlKind) -> float:
    return DEFAULT_RECRAWL_DAYS[kind] * DAY_SECONDS


def first_check(url: str, kind: UrlKind, now: float) -> float:
    """No page the batch crawl already has is fetched again now: its first check comes after one
    full period, spread over the following period so the checks do not all fall at once."""
    period = _period(kind)
    return spread_due(url, now + period, period)


def plan(crawl: Path, now: float) -> Iterator[tuple[str, QueuedUrl | None]]:
    """``(category, row)`` per URL record; row ``None`` for records left out."""
    finished_at = float(json.loads((crawl / "manifest.json").read_text())["finished_at"])
    db = sqlite3.connect(f"file:{crawl / 'state' / 'index.sqlite3'}?mode=ro", uri=True)
    try:
        fetched = {
            url: datetime.fromisoformat(at).timestamp()
            for url, at in db.execute("SELECT url, fetched_at FROM raw_pages")
        }
        for url, payload, status in db.execute("SELECT url, payload, status FROM url_records"):
            purpose = json.loads(payload).get("response_purpose")
            host = urlsplit(url).hostname or ""
            if purpose in _SKIPPED_PURPOSES or not host:
                yield "ignored", None
            elif status == "extracted":
                kind = url_kind(url)
                sent = fetched.get(url, finished_at)
                yield (
                    "extracted",
                    QueuedUrl(url, host, kind, first_check(url, kind, now), sent, 0),
                )
            elif status in {"scheduled", "failed"}:
                yield status, QueuedUrl(url, host, url_kind(url), now, None, 0)
            elif status == "discovered_not_downloaded" and recognizable_asset(url) == "document":
                due = first_check(url, "document", now)
                yield "documents", QueuedUrl(url, host, "document", due, finished_at, 0)
            else:
                yield "ignored", None
    finally:
        db.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawl", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args(argv)
    counts: Counter[str] = Counter()

    def rows() -> Iterator[QueuedUrl]:
        for category, row in plan(args.crawl, time.time()):
            counts[category] += 1
            if row is not None:
                yield row

    queue = SqliteUrlQueue(args.database)
    imported = queue.import_rows(rows())
    queue.close()
    for key in ("extracted", "scheduled", "failed", "documents", "ignored"):
        print(f"{key}={counts[key]}")
    print(f"imported={imported}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
