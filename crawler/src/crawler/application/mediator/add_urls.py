from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from crawler.application.mediator.ports import Clock, UrlQueue
from crawler.domain.mediator.kinds import url_kind
from crawler.domain.mediator.models import UrlKind
from crawler.domain.shared.links import normalize_url
from crawler.domain.shared.url import canonicalize_url


@dataclass(frozen=True, slots=True)
class AddResult:
    accepted: int
    duplicates: int
    rejected: int


def _queue_items(urls: Sequence[str]) -> tuple[dict[str, tuple[str, str, UrlKind]], int]:
    """The rows for ``urls`` by canonical URL, and how many could not be read as a URL."""
    items: dict[str, tuple[str, str, UrlKind]] = {}
    rejected = 0
    for raw in urls:
        url, host, reason = canonicalize_url(raw, check_traps=False)
        if url is None and reason == "non_default_port":
            # Any group may run a site on its own port; only the merger filters by scope.
            url = normalize_url(raw)
            host = urlsplit(url).hostname if url else None
        if url is None or host is None:
            rejected += 1
            continue
        items.setdefault(url, (url, host, url_kind(url)))
    return items, rejected


def add_urls(
    queue: UrlQueue, urls: Sequence[str], clock: Clock, *, force: bool = False
) -> AddResult:
    """Queue the URLs of one ``POST /add``; a known URL keeps its schedule unless ``force``,
    which puts it ahead of everything due."""
    items, rejected = _queue_items(urls)
    accepted = queue.add(list(items.values()), clock.now())
    if force:
        queue.make_due(list(items))
    return AddResult(accepted, len(urls) - rejected - accepted, rejected)


def cancel_urls(queue: UrlQueue, urls: Sequence[str]) -> int:
    """Take the URLs of one ``POST /add`` with ``cancel`` off the schedule."""
    items, _ = _queue_items(urls)
    return queue.cancel(list(items))
