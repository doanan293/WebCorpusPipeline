from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# NukeViet prints "2020-02-23+0723:09:00" for 2020-02-23T23:09:00+07:00.
_NUKEVIET_DATE = re.compile(r"^(\d{4}-\d\d-\d\d)([+-]\d\d)(\d\d:\d\d:\d\d)$")
# Pages without a zone are Vietnamese sites publishing local time.
_LOCAL_ZONE = timezone(timedelta(hours=7))


def parse_page_date(raw: str | None) -> str | None:
    """Return a page date as ISO 8601 with a UTC offset, or None when it is not a date."""
    value = " ".join((raw or "").split())
    if not value:
        return None
    value = _NUKEVIET_DATE.sub(r"\1T\3\2:00", value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_ZONE)
    return parsed.isoformat()
