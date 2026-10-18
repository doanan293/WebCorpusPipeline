from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from dieu_khien.application.controller.ports import Clock, UrlQueue
from dieu_khien.domain.controller.kinds import url_kind
from dieu_khien.domain.controller.models import UrlKind
from dieu_khien.domain.shared.links import normalize_url
from dieu_khien.domain.shared.url import canonicalize_url


@dataclass(frozen=True, slots=True)
class AddResult:
    accepted: int
    duplicates: int
    rejected: int


def add_urls(queue: UrlQueue, urls: Sequence[str], clock: Clock) -> AddResult:
    """Queue the URLs of one ``POST /add``; a known URL keeps its schedule."""
    items: dict[str, tuple[str, str, UrlKind]] = {}
    rejected = 0
    for raw in urls:
        url, host, reason = canonicalize_url(raw, check_traps=False)
        if url is None and reason == "non_default_port":
            # Any group may run a site on its own port; only the aggregator filters by scope.
            url = normalize_url(raw)
            host = urlsplit(url).hostname if url else None
        if url is None or host is None:
            rejected += 1
            continue
        items.setdefault(url, (url, host, url_kind(url)))
    accepted = queue.add(list(items.values()), clock.now())
    return AddResult(accepted, len(urls) - rejected - accepted, rejected)
