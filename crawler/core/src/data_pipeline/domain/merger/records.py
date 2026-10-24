from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_STUB_FIELDS = ("url", "format", "title", "out-links", "content_id")


def duplicate_stub(record: Mapping[str, Any], owner_url: str) -> dict[str, Any]:
    """A page whose text another URL already owns: no text, but its links stay in the graph."""
    stub = {name: record[name] for name in _STUB_FIELDS if name in record}
    stub["content"] = ""
    stub["duplicate_of"] = owner_url
    return stub
