"""One-off: give the aggregator the URLs and content owners the batch crawl already knows.

Reads ``<crawl>/state/index.sqlite3`` read-only. Delete after use.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

from data_pipeline.infrastructure.aggregator.sqlite_state import SqliteAggregatorState


def known_urls(db: sqlite3.Connection) -> Iterator[str]:
    for url, payload in db.execute("SELECT url, payload FROM url_records"):
        yield url
        final_url = json.loads(payload).get("final_url")
        if final_url and final_url != url:
            yield final_url


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawl", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args(argv)
    source = sqlite3.connect(f"file:{args.crawl / 'state' / 'index.sqlite3'}?mode=ro", uri=True)
    state = SqliteAggregatorState(args.database)
    try:
        state.import_seen(known_urls(source))
        state.import_owners(source.execute("SELECT content_id, url FROM content_owner"))
        (seen,) = state.db.execute("SELECT COUNT(*) FROM seen_urls").fetchone()
        (owners,) = state.db.execute("SELECT COUNT(*) FROM content_owners").fetchone()
    finally:
        source.close()
        state.close()
    print(f"seen={seen}")
    print(f"owners={owners}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
