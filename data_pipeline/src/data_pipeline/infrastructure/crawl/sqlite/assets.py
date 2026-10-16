from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from data_pipeline.domain.crawl.frontier.assets import AssetRole


class SqliteAssets:
    """The ``Assets`` port over ``files`` and ``asset_referrers``."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def file_record(self, url: str) -> dict[str, object] | None:
        row = self.connection.execute("SELECT payload FROM files WHERE url = ?;", (url,)).fetchone()
        return json.loads(row[0]) if row else None

    def add_asset_referrer(self, url: str, page_url: str, role: AssetRole) -> None:
        self.connection.execute(
            """
            INSERT INTO asset_referrers (url, page_url, role)
            VALUES (?, ?, ?)
            ON CONFLICT(url, page_url, role) DO NOTHING;
            """,
            (url, page_url, role),
        )
        cursor = self.connection.execute("SELECT payload FROM files WHERE url = ?;", (url,))
        row = cursor.fetchone()
        if row:
            payload = json.loads(row[0])
            payload["referring_pages"] = list(self.asset_referrers(url))
            new_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            self.connection.execute(
                "UPDATE files SET payload = ? WHERE url = ?;",
                (new_payload, url),
            )

    def asset_referrers(self, url: str) -> tuple[str, ...]:
        cursor = self.connection.execute(
            "SELECT DISTINCT page_url FROM asset_referrers WHERE url = ? ORDER BY page_url ASC;",
            (url,),
        )
        return tuple(row[0] for row in cursor.fetchall())
