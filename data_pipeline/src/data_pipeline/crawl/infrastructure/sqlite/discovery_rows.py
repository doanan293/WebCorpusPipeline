from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from data_pipeline.crawl.domain.models import RouteFamilyState

if TYPE_CHECKING:
    from data_pipeline.crawl.infrastructure.sqlite.state_store import SqliteCrawlState


def put_route_family(state: SqliteCrawlState, family: RouteFamilyState) -> None:
    payload = json.dumps(
        {
            "family_key": family.family_key,
            "kind": family.kind,
            "scheduled_next_ordinal": family.scheduled_next_ordinal,
            "content_fingerprints": list(family.content_fingerprints),
            "target_fingerprints": list(family.target_fingerprints),
            "accepted_targets": list(family.accepted_targets),
            "consecutive_zero_novelty": family.consecutive_zero_novelty,
            "closed": family.closed,
            "closure_reason": family.closure_reason,
        },
        ensure_ascii=False,
    )
    state.connection.execute(
        """
        INSERT INTO route_families (family_key, payload) VALUES (?, ?)
        ON CONFLICT(family_key) DO UPDATE SET payload = excluded.payload;
        """,
        (family.family_key, payload),
    )


def iter_route_families(state: SqliteCrawlState) -> Iterator[RouteFamilyState]:
    cursor = state.connection.execute("SELECT payload FROM route_families ORDER BY family_key ASC;")
    for (payload_str,) in cursor.fetchall():
        d = json.loads(payload_str)
        yield RouteFamilyState(
            family_key=d["family_key"],
            kind=d["kind"],
            scheduled_next_ordinal=d["scheduled_next_ordinal"],
            content_fingerprints=tuple(d["content_fingerprints"]),
            target_fingerprints=tuple(d["target_fingerprints"]),
            accepted_targets=tuple(d["accepted_targets"]),
            consecutive_zero_novelty=d["consecutive_zero_novelty"],
            closed=d["closed"],
            closure_reason=d["closure_reason"],
        )


def upsert_host_discovery(state: SqliteCrawlState, record: Mapping[str, object]) -> None:
    hostname = str(record["hostname"])
    payload = json.dumps(dict(record), ensure_ascii=False)
    state.connection.execute(
        """
        INSERT INTO host_discovery (hostname, payload)
        VALUES (?, ?)
        ON CONFLICT(hostname) DO UPDATE SET payload = excluded.payload;
        """,
        (hostname, payload),
    )


def iter_host_discovery(state: SqliteCrawlState) -> Iterator[dict[str, object]]:
    cursor = state.connection.execute("SELECT payload FROM host_discovery ORDER BY hostname;")
    for row in cursor:
        yield json.loads(row[0])


def host_completion(
    state: SqliteCrawlState, hostnames: frozenset[str]
) -> dict[str, dict[str, object]]:
    extracted = Counter()
    terminal_reasons: dict[str, Counter[str]] = defaultdict(Counter)
    for record in state.iter_url_records():
        hostname = (urlsplit(str(record.get("url", ""))).hostname or "").lower().rstrip(".")
        if hostname not in hostnames:
            continue
        if record.get("status") in {"extracted", "file_saved"}:
            extracted[hostname] += 1
        reason = record.get("reason")
        if reason and record.get("status") in {"skipped", "failed"}:
            terminal_reasons[hostname][str(reason)] += 1

    return {
        hostname: {
            "status": "complete" if extracted[hostname] else "zero_content",
            "extracted_pages": extracted[hostname],
            "terminal_reasons": dict(sorted(terminal_reasons[hostname].items())),
        }
        for hostname in sorted(hostnames)
    }
