from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import TYPE_CHECKING

from data_pipeline.infrastructure.crawl.sqlite import discovery_rows

if TYPE_CHECKING:
    from data_pipeline.domain.crawl.models import RouteFamilyState


class SqliteRouteFamilies:
    """The ``RouteFamilies`` port over ``route_families``."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def route_families(self) -> Iterator[RouteFamilyState]:
        return discovery_rows.iter_route_families(self.connection)

    def put_route_family(self, family: RouteFamilyState) -> None:
        discovery_rows.put_route_family(self.connection, family)
