from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from data_pipeline.application.merger.ports import ContentOwners, SeenUrls, Target
from data_pipeline.domain.merger.batching import UrlBatcher
from data_pipeline.domain.merger.content import content_key
from data_pipeline.domain.merger.links import candidate_urls, canonical_url
from data_pipeline.domain.merger.records import duplicate_stub


@dataclass(frozen=True, slots=True)
class Scope:
    """The hosts a crawl may spread over (those of its seeds), and non-default ports allowed."""

    allowed_hosts: frozenset[str]
    allowed_ports: frozenset[int] = frozenset()


async def aggregate(
    record: dict[str, Any],
    *,
    seen: SeenUrls,
    owners: ContentOwners,
    deliver: Callable[[Target, dict[str, Any]], Awaitable[None]],
    batcher: UrlBatcher,
    scope: Scope,
    now: float,
) -> None:
    """Store one record (a stub when another URL owns its text) and queue its new links.

    A duplicate (its text owned by another URL) is a dead end, as in the batch crawler: otherwise a
    pager that ignores ``?page`` (every ``?page=N`` echoing the same page and linking to more of
    them) is followed without end. A page without text still spreads (a JavaScript hub page).
    """
    url = record["url"]
    page_url = canonical_url(url, scope.allowed_ports)[0]
    if page_url is not None:
        seen.add_new([page_url])
    key = content_key(record["content"])
    owner = owners.claim(key, url) if key is not None else url
    await deliver("storage", record if owner == url else duplicate_stub(record, owner))
    if owner != url:
        return
    links = candidate_urls(
        record["out-links"],
        source_url=url,
        allowed_hosts=scope.allowed_hosts,
        allowed_ports=scope.allowed_ports,
    )
    for batch in batcher.add(seen.add_new(links), now):
        await deliver("mediator", {"urls": batch})
