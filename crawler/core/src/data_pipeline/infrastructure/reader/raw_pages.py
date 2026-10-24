from __future__ import annotations

import gzip
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from data_pipeline.domain.reader.models import StoredPage


class SqliteRawPages:
    """The ``RawPages`` port over the batch crawl's ``raw_pages`` and ``html/`` (read only)."""

    def __init__(self, crawl: Path) -> None:
        self.crawl = crawl

    def iter(self) -> Iterator[StoredPage]:
        uri = f"file:{self.crawl / 'state' / 'index.sqlite3'}?mode=ro"
        db = sqlite3.connect(uri, uri=True)
        try:
            rows = db.execute(
                "SELECT url, final_url, content_type, body_sha256 FROM raw_pages ORDER BY url"
            )
            for url, final_url, content_type, sha256 in rows:
                path = self.crawl / "html" / sha256[:2] / f"{sha256}.html.gz"
                if path.exists():
                    yield StoredPage(
                        url, final_url, content_type, gzip.decompress(path.read_bytes())
                    )
        finally:
            db.close()
