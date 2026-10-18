from __future__ import annotations

import hashlib
import json

# Part of the LlamaParse cache key. Bump it when the way cloud results are read changes, so stale
# cached results are not reused ("page-text": text is read per page from the "text" expansion).
CLOUD_CACHE_REVISION = "fast/latest/page-text"


def cache_key(sha256: str, indices: list[int], parser: str, settings_digest: str = "") -> str:
    payload = json.dumps([sha256, sorted(indices), parser, settings_digest], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()
