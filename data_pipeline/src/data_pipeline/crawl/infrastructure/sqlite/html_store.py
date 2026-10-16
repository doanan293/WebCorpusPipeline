from __future__ import annotations

import gzip
import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_GZIP_LEVEL = 6


@dataclass(frozen=True, slots=True)
class RawPage:
    url: str
    final_url: str
    status: int
    content_type: str
    fetched_at: str
    body: bytes


class HtmlStore:
    """Keep each fetched HTML body gzipped once per distinct body, indexed by URL in SQLite.

    Re-extracting from these files lets an extraction fix reach every page without a re-crawl.
    """

    def __init__(self, root: Path, connection: sqlite3.Connection) -> None:
        self.root = root
        self.connection = connection

    def _path(self, sha256: str) -> Path:
        return self.root / sha256[:2] / f"{sha256}.html.gz"

    def put(
        self,
        url: str,
        body: bytes,
        *,
        final_url: str,
        status: int,
        content_type: str,
        fetched_at: str,
    ) -> str:
        sha256 = hashlib.sha256(body).hexdigest()
        path = self._path(sha256)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(gzip.compress(body, compresslevel=_GZIP_LEVEL))
            temporary.replace(path)
        self.connection.execute(
            """
            INSERT INTO raw_pages (url, final_url, status, content_type, fetched_at, body_sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              final_url = excluded.final_url,
              status = excluded.status,
              content_type = excluded.content_type,
              fetched_at = excluded.fetched_at,
              body_sha256 = excluded.body_sha256;
            """,
            (url, final_url, status, content_type, fetched_at, sha256),
        )
        return sha256

    def get(self, url: str) -> RawPage | None:
        row = self.connection.execute(
            """
            SELECT final_url, status, content_type, fetched_at, body_sha256
            FROM raw_pages WHERE url = ?;
            """,
            (url,),
        ).fetchone()
        if row is None:
            return None
        final_url, status, content_type, fetched_at, sha256 = row
        return RawPage(
            url=url,
            final_url=str(final_url),
            status=int(status),
            content_type=str(content_type),
            fetched_at=str(fetched_at),
            body=gzip.decompress(self._path(str(sha256)).read_bytes()),
        )
