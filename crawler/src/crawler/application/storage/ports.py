from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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

    def existing_html(self, urls: list[str]) -> set[str]:
        """Those of ``urls`` that have an ``html`` record."""
        ...

    def link_texts(self, dests: list[str]) -> dict[str, dict[str, int]]:
        """For each of ``dests``: how often each text (``intro``) of its incoming edges occurs."""
        ...

    def set_intros(self, intros: dict[str, str]) -> None:
        """Set ``html.intro`` of each URL (all records exist)."""
        ...

    def add_pictures(self, rows: list[dict[str, Any]]) -> None:
        """Insert ``picture`` records whose ``url`` is new; stored ones stay as they are."""
        ...

    def first_linking_page(self, dest: str) -> str:
        """Of the pages linking to ``dest``, the one stored first; ``""`` when none links to it."""
        ...

    def origin(self, url: str) -> str | None:
        """The ``origin`` already stored on the ``other`` record of ``url``, if any."""
        ...

    def touch(self, url: str, checked_at: str) -> None:
        """Set only ``checked_at`` on the ``html`` or ``other`` record of ``url``."""
        ...

    def ensure_indexes(self) -> None: ...

    def close(self) -> None:
        """Release the connection (its threads otherwise linger until the process exits)."""
        ...


class ImageFetchError(Exception):
    """An image could not be downloaded; the message is a short reason such as ``timeout``."""


@dataclass(frozen=True, slots=True)
class ImageResult:
    status: int
    content_type: str
    body: bytes  # at most MAX_PICTURE_BYTES + 1 bytes: one more means too large


class ImageGetter(Protocol):
    async def get(self, url: str) -> ImageResult: ...

    async def close(self) -> None: ...


class PictureStore(Protocol):
    def pending(self, *, retry_errors: bool) -> Iterable[str]:
        """URLs of ``picture`` records without ``b64`` (and without ``error`` unless retrying)."""
        ...

    def save_picture(
        self,
        url: str,
        *,
        b64: str,
        fmt: str,
        size: int | None,
        error: str | None,
        fetched_at: str,
    ) -> None: ...
