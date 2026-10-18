from __future__ import annotations

from typing import Any, Protocol

from data_pipeline.domain.shared.errors import ExternalFailure


class StoreUnavailableError(ExternalFailure):
    """The database cannot be reached right now; the sender should try again later."""


class StoreRejectedError(ExternalFailure):
    """The database refused this record for good (too large, invalid); resending will not help."""


class CorpusStore(Protocol):
    def upsert(self, collection: str, document: dict[str, Any]) -> None: ...

    def replace_edges(self, src: str, edges: list[dict[str, Any]]) -> None:
        """Make ``edges`` the only graph edges leaving ``src``."""
        ...

    def ensure_indexes(self) -> None: ...
