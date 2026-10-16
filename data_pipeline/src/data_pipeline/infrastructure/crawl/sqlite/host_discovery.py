from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping

from data_pipeline.infrastructure.crawl.sqlite import discovery_rows


class SqliteHostDiscovery:
    """The ``HostDiscovery`` port over ``host_discovery`` and the discovery ``targets``."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def host_discovery(self) -> Iterator[dict[str, object]]:
        return discovery_rows.iter_host_discovery(self.connection)

    def upsert_host_discovery(self, record: Mapping[str, object]) -> None:
        discovery_rows.upsert_host_discovery(self.connection, record)

    def host_completion(self, hostnames: frozenset[str]) -> dict[str, dict[str, object]]:
        return discovery_rows.host_completion(self.connection, hostnames)

    def put_target(self, record: Mapping[str, object]) -> None:
        url = str(record["url"])
        self.connection.execute(
            "INSERT OR REPLACE INTO targets (url, payload) VALUES (?, ?);",
            (url, json.dumps(dict(record), sort_keys=True, ensure_ascii=False)),
        )
