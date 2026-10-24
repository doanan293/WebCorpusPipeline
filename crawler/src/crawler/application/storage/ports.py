from __future__ import annotations

from typing import Any, Protocol

from crawler.domain.shared.errors import ExternalFailure


class StoreUnavailableError(ExternalFailure):
    """The database cannot be reached right now; the sender should try again later."""


class StoreRejectedError(ExternalFailure):
    """The database refused this record for good (too large, invalid); resending will not help."""


class CorpusStore(Protocol):
    def upsert(self, collection: str, document: dict[str, Any]) -> None: ...

    def replace_edges(self, src: str, edges: list[dict[str, Any]]) -> None:
        """Make ``edges`` the only graph edges leaving ``src``."""
        ...

    def incoming_intros(self, dest: str) -> list[str]:
        """The link text (``intro``) of every ``graph`` edge pointing at ``dest``."""
        ...

    def set_intro(self, url: str, intro: str) -> bool:
        """Set ``html.intro`` of ``url``; ``False`` when no such record exists."""
        ...

    def touch(self, url: str, checked_at: str) -> None:
        """Set only ``checked_at`` on the ``html`` or ``other`` record of ``url``."""
        ...

    def ensure_indexes(self) -> None: ...

    def close(self) -> None:
        """Release the connection (its threads otherwise linger until the process exits)."""
        ...
