from __future__ import annotations

import functools
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, Concatenate


class LockedRepository:
    """A repository over the work state's shared connection and its shared write lock."""

    def __init__(self, conn: sqlite3.Connection, write_lock: AbstractContextManager[Any]) -> None:
        self.conn = conn
        # LlamaParse workers record their jobs from other threads; every write takes this lock.
        self._write_lock = write_lock


def locked[S: LockedRepository, **P, R](
    method: Callable[Concatenate[S, P], R],
) -> Callable[Concatenate[S, P], R]:
    """Run a write method while holding the work state's write lock."""

    @functools.wraps(method)
    def wrapper(self: S, *args: P.args, **kwargs: P.kwargs) -> R:
        with self._write_lock:
            return method(self, *args, **kwargs)

    return wrapper
