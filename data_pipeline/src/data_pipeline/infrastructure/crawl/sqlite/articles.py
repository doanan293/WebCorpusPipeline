from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Mapping

from data_pipeline.infrastructure.crawl.sqlite import export


class SqliteArticles:
    """The ``Articles`` port over the ``articles`` table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def article(self, url: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT payload FROM articles WHERE url = ?;", (url,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def set_article_payload(self, url: str, article: Mapping[str, object]) -> None:
        self.connection.execute(
            "UPDATE articles SET payload = ? WHERE url = ?;",
            (json.dumps(dict(article), ensure_ascii=False), url),
        )

    def reextract_candidates(self) -> list[tuple[str, object]]:
        """(url, content_id) of extracted pages whose raw HTML is stored."""
        rows = self.connection.execute(
            """
            SELECT r.url, json_extract(r.payload, '$.content_id')
            FROM url_records AS r JOIN raw_pages AS p ON p.url = r.url
            WHERE r.status = 'extracted' ORDER BY r.url;
            """
        ).fetchall()
        return [(str(url), content_id) for url, content_id in rows]

    def navigation_pages(self) -> list[str]:
        rows = self.connection.execute(
            """
            SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
            WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'navigation'
            ORDER BY a.url;
            """
        )
        return [str(row[0]) for row in rows]

    def recent_content_candidates(self) -> list[tuple[str, object, object, object]]:
        """(url, published_at, modified_at, first_seen_run) of extracted content pages."""
        rows = self.connection.execute(
            """
            SELECT a.url,
                   json_extract(a.payload, '$.published_at'),
                   json_extract(a.payload, '$.modified_at'),
                   json_extract(r.payload, '$.first_seen_run')
            FROM articles AS a JOIN url_records AS r ON r.url = a.url
            WHERE r.status = 'extracted' AND json_extract(a.payload, '$.page_role') = 'content';
            """
        )
        return [(str(url), published, modified, first) for url, published, modified, first in rows]

    def iter_final_articles(self, urls: Iterable[str] | None = None) -> Iterator[tuple[str, str]]:
        return export.iter_final_articles(self.connection, urls)
