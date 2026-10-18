from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from data_pipeline.domain.aggregator.links import canonical_url
from data_pipeline.domain.shared.hostnames import normalize_hostname


@dataclass(frozen=True, slots=True)
class Seeds:
    """Where a crawl starts, and the hosts it may spread over."""

    urls: tuple[str, ...]
    hosts: frozenset[str]
    problems: tuple[str, ...] = ()


def parse_seeds(lines: Iterable[str], allowed_ports: frozenset[int] = frozenset()) -> Seeds:
    """One seed per line: a hostname starts at ``https://<host>/``, a URL starts at itself.

    Blank lines and ``#`` comments are skipped; a line that is neither is reported in
    ``problems`` (``"line N: ..."``) and left out.
    """
    urls: dict[str, None] = {}
    hosts: set[str] = set()
    problems: list[str] = []
    for number, raw in enumerate(lines, 1):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if "://" in value:
            url, host = canonical_url(value, allowed_ports)
            if url is None or host is None or not value.lower().startswith(("http://", "https://")):
                problems.append(f"line {number}: not an http(s) URL we can crawl: {value}")
                continue
            urls[url] = None
            hosts.add(host)
            continue
        try:
            host = normalize_hostname(value.rstrip("/"))
        except ValueError:
            problems.append(f"line {number}: neither a hostname nor an http(s) URL: {value}")
            continue
        urls[f"https://{host}/"] = None
        hosts.add(host)
    return Seeds(tuple(urls), frozenset(hosts), tuple(problems))
