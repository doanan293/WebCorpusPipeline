from __future__ import annotations

import re

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_hostname(value: str) -> str:
    hostname = value.strip().lower().rstrip(".")
    labels = hostname.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels):  # noqa: PLR2004 -- a name and a TLD
        msg = f"expected hostname, got: {value!r}"
        raise ValueError(msg)
    return hostname
