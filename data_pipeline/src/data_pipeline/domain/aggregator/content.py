from __future__ import annotations

import hashlib


def content_key(text: str) -> str | None:
    """The hash duplicate pages share; ``None`` for empty text, which is never a duplicate."""
    stripped = text.strip()
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest() if stripped else None
