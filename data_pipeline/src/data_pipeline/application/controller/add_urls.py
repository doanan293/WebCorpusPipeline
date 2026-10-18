from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from data_pipeline.application.controller.ports import Clock, UrlQueue
from data_pipeline.domain.controller.kinds import url_kind
from data_pipeline.domain.controller.models import UrlKind
from data_pipeline.domain.shared.url import canonicalize_url


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
        url, host, _reason = canonicalize_url(raw, check_traps=False)
        if url is None or host is None:
            rejected += 1
            continue
        items.setdefault(url, (url, host, url_kind(url)))
    accepted = queue.add(list(items.values()), clock.now())
    return AddResult(accepted, len(urls) - rejected - accepted, rejected)
