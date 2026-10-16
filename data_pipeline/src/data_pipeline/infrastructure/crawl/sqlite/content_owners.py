from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from data_pipeline.domain.crawl.models import DUPLICATE_DROPPED_FIELDS
from data_pipeline.infrastructure.crawl.sqlite.transaction import immediate_transaction


def _url_record(connection: sqlite3.Connection, url: str) -> dict[str, object] | None:
    row = connection.execute("SELECT payload FROM url_records WHERE url = ?;", (url,)).fetchone()
    return json.loads(row[0]) if row else None


class SqliteContentOwners:
    """The ``ContentOwners`` port over ``content_owner``; a release rewrites the duplicates."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def claim_content(self, content_id: str, url: str) -> str:
        self.connection.execute(
            """
            INSERT INTO content_owner (content_id, url)
            VALUES (?, ?)
            ON CONFLICT(content_id) DO NOTHING;
            """,
            (content_id, url),
        )
        self.connection.commit()
        cursor = self.connection.execute(
            "SELECT url FROM content_owner WHERE content_id = ?;", (content_id,)
        )
        row = cursor.fetchone()
        if row is None:
            msg = f"content owner missing after claim: {content_id}"
            raise RuntimeError(msg)
        return str(row[0])

    def release_content_owner(self, url: str, content_id: str) -> None:
        """Hand ownership of content_id from url to its first duplicate, if any."""
        owner = self.connection.execute(
            "SELECT url FROM content_owner WHERE content_id = ?;", (content_id,)
        ).fetchone()
        if owner is None or owner[0] != url:
            return
        article_row = self.connection.execute(
            "SELECT payload FROM articles WHERE url = ?;", (url,)
        ).fetchone()
        old_article: dict[str, Any] = json.loads(article_row[0]) if article_row else {}
        owner_content = {
            field: old_article[field]
            for field in ("text", *DUPLICATE_DROPPED_FIELDS)
            if field in old_article
        }
        dependents = [
            str(row[0])
            for row in self.connection.execute(
                """
                SELECT a.url FROM articles AS a JOIN url_records AS r ON r.url = a.url
                WHERE json_extract(a.payload, '$.duplicate_of') = ?
                  AND json_extract(r.payload, '$.content_id') = ?
                ORDER BY a.url;
                """,
                (url, content_id),
            )
        ]
        with immediate_transaction(self.connection):
            if not dependents:
                self.connection.execute(
                    "DELETE FROM content_owner WHERE content_id = ?;", (content_id,)
                )
            else:
                new_owner = dependents[0]
                self.connection.execute(
                    "UPDATE content_owner SET url = ? WHERE content_id = ?;",
                    (new_owner, content_id),
                )
                for dependent in dependents:
                    article = json.loads(
                        self.connection.execute(
                            "SELECT payload FROM articles WHERE url = ?;", (dependent,)
                        ).fetchone()[0]
                    )
                    record = _url_record(self.connection, dependent) or {"url": dependent}
                    if dependent == new_owner:
                        article.pop("duplicate_of", None)
                        article.update(owner_content)
                        record.pop("duplicate_of", None)
                    else:
                        article["duplicate_of"] = new_owner
                        record["duplicate_of"] = new_owner
                    self.connection.execute(
                        "UPDATE articles SET payload = ? WHERE url = ?;",
                        (json.dumps(article, ensure_ascii=False), dependent),
                    )
                    self.connection.execute(
                        "UPDATE url_records SET payload = ? WHERE url = ?;",
                        (json.dumps(record, ensure_ascii=False), dependent),
                    )

    def content_owners(self) -> dict[str, str]:
        rows = self.connection.execute("SELECT content_id, url FROM content_owner;")
        return {str(content_id): str(url) for content_id, url in rows}

    def replace_content_owners(self, urls: Iterable[str], owners: Mapping[str, str]) -> None:
        """Drop the ownership rows of ``urls``, then store ``owners`` (content_id -> url)."""
        self.connection.executemany(
            "DELETE FROM content_owner WHERE url = ?;", ((url,) for url in urls)
        )
        self.connection.executemany(
            "INSERT OR REPLACE INTO content_owner (content_id, url) VALUES (?, ?);",
            owners.items(),
        )
