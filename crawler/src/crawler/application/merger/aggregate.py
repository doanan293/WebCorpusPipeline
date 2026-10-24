from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from crawler.application.merger.ports import ContentOwners, Fingerprints, SeenUrls, Target
from crawler.domain.merger.batching import UrlBatcher
from crawler.domain.merger.change import classify
from crawler.domain.merger.content import content_key
from crawler.domain.merger.links import candidate_urls, canonical_url
from crawler.domain.merger.records import duplicate_stub
from crawler.domain.shared.change import UNCHANGED, Change


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
    fingerprints: Fingerprints,
    deliver: Callable[[Target, dict[str, Any]], Awaitable[None]],
    batcher: UrlBatcher,
    scope: Scope,
    now: float,
) -> None:
    """Store one record (a stub when another URL owns its text) and queue its new links.

    A duplicate (its text owned by another URL) is a dead end, as in the batch crawler: otherwise a
    pager that ignores ``?page`` (every ``?page=N`` echoing the same page and linking to more of
    them) is followed without end. A page without text still spreads (a JavaScript hub page).

    A recrawl whose content did not change (or that the site answered with 304) only touches the
    stored record and does not spread again. Every visit is reported to the mediator, under the
    URL it queued, so it can adapt the page's schedule.
    """
    url = record["url"]
    change: Change
    if record.get("format") == "not_modified":
        change = "not_modified"
    else:
        change = classify(fingerprints.fingerprint(url), record.get("fingerprint"))
    report = {
        "url": record.get("requested_url") or url,
        "change": change,
        "etag": record.get("etag"),
        "last_modified": record.get("last_modified"),
    }
    if change in UNCHANGED:
        await deliver("storage", {"url": url, "change": change})
        await deliver("outcome", {"reports": [report]})
        return
    if fingerprint := record.get("fingerprint"):
        fingerprints.save_fingerprint(url, fingerprint)
    record = {**record, "change": change}
    await _spread(
        record,
        url,
        seen=seen,
        owners=owners,
        deliver=deliver,
        batcher=batcher,
        scope=scope,
        now=now,
    )
    await deliver("outcome", {"reports": [report]})


async def _spread(
    record: dict[str, Any],
    url: str,
    *,
    seen: SeenUrls,
    owners: ContentOwners,
    deliver: Callable[[Target, dict[str, Any]], Awaitable[None]],
    batcher: UrlBatcher,
    scope: Scope,
    now: float,
) -> None:
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
