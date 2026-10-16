from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def immediate_transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Run the block in one write transaction; roll it back on any exception."""
    connection.execute("BEGIN IMMEDIATE;")
    try:
        yield
        connection.execute("COMMIT;")
    except BaseException:
        connection.execute("ROLLBACK;")
        raise
