"""HTTP validators of a URL's last download: kept by the mediator, sent by the downloader."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Validators:
    """What the last download of a URL said, sent back as a conditional request (304 if same)."""

    etag: str | None
    last_modified: str | None
