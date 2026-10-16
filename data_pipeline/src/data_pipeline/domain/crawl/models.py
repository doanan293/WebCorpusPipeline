from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from data_pipeline.domain.crawl.frontier.assets import AssetGroup, AssetRole

# Fields a duplicate page's stored article drops: its owner holds the same content.
DUPLICATE_DROPPED_FIELDS = ("content_html", "content_blocks", "headings", "observed_refs")

FamilyKind = Literal["pagination", "search_filter", "calendar_archive", "action_auth"]


@dataclass(frozen=True, slots=True)
class RouteFamilyState:
    family_key: str
    kind: FamilyKind
    scheduled_next_ordinal: int | None
    content_fingerprints: tuple[str, ...]
    target_fingerprints: tuple[str, ...]
    accepted_targets: tuple[str, ...]
    consecutive_zero_novelty: int
    closed: bool
    closure_reason: str | None


@dataclass(frozen=True, slots=True)
class FamilyObservation:
    family_key: str
    kind: FamilyKind
    ordinal: int | None
    content_fingerprint: str
    target_fingerprint: str
    content_targets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReusedAssetTarget:
    url: str
    role: AssetRole
    group: AssetGroup
    referring_pages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RawPage:
    url: str
    final_url: str
    status: int
    content_type: str
    fetched_at: str
    body: bytes
